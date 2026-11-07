"""Shared Odds API credit ledger for this experiment.

The cap counts credits, not HTTP requests. marv/props/odds.py documents the
current event-odds cost as 1 credit per market per region. The events list is
documented as free. Historical odds are not used here.

A reservation of that worst case is persisted before the call. The response
headers can raise the counted cost. They cannot lower it. A missing or
unparseable cost fails closed and does not retry.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

LEDGER_NAME = "usage.json"
# Current event odds: 1 credit per market per region (marv/props/odds.py).
EVENT_ODDS_CREDITS_PER_MARKET_PER_REGION = 1
# Upcoming events list: documented free call in marv/props/odds.py.
EVENTS_LIST_CREDITS = 0
DEFAULT_DAILY_CAP = 100


class CreditError(RuntimeError):
    pass


def event_odds_worst_case(n_markets: int, n_regions: int = 1) -> int:
    if n_markets < 1 or n_regions < 1:
        raise CreditError("refusing a billable call with an unknown cost")
    return n_markets * n_regions * EVENT_ODDS_CREDITS_PER_MARKET_PER_REGION


def _today(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise CreditError("ledger clock must be timezone-aware")
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _empty(day: str, cap: int) -> dict:
    return {
        "schema": "muse-prop1.credit-ledger.v1",
        "date": day,
        "cap": cap,
        "used": 0,
        "fail_closed": False,
        "fail_reason": "",
        "last_remaining": None,
        "reservations": [],
    }


class Ledger:
    """File lock around one JSON ledger. `used` never decreases."""

    def __init__(self, path: Path, cap: int = DEFAULT_DAILY_CAP, now: datetime | None = None):
        if cap < 1:
            raise CreditError("credit cap must be positive")
        self.path = Path(path)
        self.cap = cap
        self.now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "Ledger":
        if fcntl is None:
            raise CreditError("file locking is unavailable; refusing to track credits")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        if fcntl is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict:
        self._fh.seek(0)
        raw = self._fh.read()
        day = _today(self.now)
        if not raw.strip():
            return _empty(day, self.cap)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CreditError("credit ledger is unreadable; failing closed") from exc
        if data.get("date") != day:
            return _empty(day, self.cap)
        data["cap"] = self.cap
        return data

    def fail_unfinished(self) -> dict:
        """A held reservation means a prior process stopped before reconcile."""
        with self:
            data = self._read()
            if any(row.get("status") == "held" for row in data.get("reservations") or []):
                data["fail_closed"] = True
                data["fail_reason"] = data.get("fail_reason") or "held reservation from an unfinished call"
                self._write(data)
            return data

    def _write(self, data: dict) -> None:
        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(text)
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def snapshot(self) -> dict:
        with self:
            return self._read()

    def reserve(self, kind: str, worst_case: int, detail: str) -> str:
        """Persist the hold before any network call. Raises CreditError if it cannot."""
        if worst_case < 0:
            raise CreditError("negative reservation")
        with self:
            data = self._read()
            if data.get("fail_closed"):
                raise CreditError(data.get("fail_reason") or "credit ledger is fail-closed")
            if data["used"] + worst_case > data["cap"]:
                raise CreditError(
                    f"reservation of {worst_case} would pass the shared cap of {data['cap']} "
                    f"(used {data['used']})")
            rid = f"{data['date']}-{len(data['reservations']) + 1}"
            data["used"] += worst_case
            data["reservations"].append({
                "id": rid, "kind": kind, "worst_case": worst_case, "counted": worst_case,
                "status": "held", "detail": detail, "header_last": None, "header_remaining": None,
            })
            self._write(data)
            return rid

    def reconcile(self, reservation_id: str, headers: dict | None, error: str | None = None) -> dict:
        """Apply response headers. Never lowers the counted cost. No retry is started here."""
        with self:
            data = self._read()
            row = next((item for item in data["reservations"] if item["id"] == reservation_id), None)
            if row is None or row.get("status") != "held":
                data["fail_closed"] = True
                data["fail_reason"] = "reservation missing at reconcile"
                self._write(data)
                raise CreditError(data["fail_reason"])
            parsed = _header_cost(headers)
            if error or parsed is None:
                row["status"] = "unknown"
                data["fail_closed"] = True
                data["fail_reason"] = error or "odds usage header missing or unparseable"
                self._write(data)
                return data
            actual, remaining = parsed
            drop = None
            if data.get("last_remaining") is not None and remaining is not None:
                drop = int(data["last_remaining"]) - int(remaining)
                if drop > actual:
                    actual = drop
            if actual < 0:
                row["status"] = "unknown"
                data["fail_closed"] = True
                data["fail_reason"] = "negative usage header"
                self._write(data)
                return data
            row["header_last"] = parsed[0]
            row["header_remaining"] = remaining
            if remaining is not None:
                data["last_remaining"] = remaining
            counted = max(int(row["worst_case"]), actual)
            extra = counted - int(row["counted"])
            if extra > 0:
                data["used"] += extra
            row["counted"] = counted
            row["status"] = "reconciled"
            if data["used"] > data["cap"]:
                data["fail_closed"] = True
                data["fail_reason"] = "reconciled usage passed the shared cap"
            self._write(data)
            return data


def _header_cost(headers: dict | None) -> tuple[int, int | None] | None:
    if not headers:
        return None
    lowered = {str(key).lower(): value for key, value in headers.items()}
    raw = lowered.get("x-requests-last")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        actual = int(str(raw).strip())
    except ValueError:
        return None
    remaining_raw = lowered.get("x-requests-remaining")
    remaining = None
    if remaining_raw is not None and str(remaining_raw).strip() != "":
        try:
            remaining = int(str(remaining_raw).strip())
        except ValueError:
            return None
    return actual, remaining
