"""Durable intent before any submit.

A client order id is derived from the decision id and committed before the
transport is called. A lost response leaves the intent unknown and does not
POST again. Cancel responses are not treated as proof. A reconcile snapshot
that disagrees with the ledger blocks further submits instead of guessing
that an order filled or was cancelled.

No method in this module opens a network connection.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .host import assert_demo_base

_NAMESPACE = uuid.UUID("6f1b9a3e-4c2d-5e70-8a11-0d4e5f607182")
_TOKEN_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
_SUBMITTED = frozenset({
    "submitting", "submit_unknown", "ack_unknown", "accepted", "partial", "filled",
    "cancel_requested", "cancel_unknown", "cancelled",
})
_EXPLICIT_CANCEL = frozenset({"canceled", "cancelled"})
_EXPLICIT_FILL = frozenset({"executed", "filled"})
_RESTING = frozenset({"resting", "open"})


class SubmitBlocked(RuntimeError):
    pass


class NoRealPolymarketOrderApi(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 96 or value.lower() in {"unknown", "live"}:
        raise ValueError(name)
    if any(ch not in _TOKEN_OK for ch in value):
        raise ValueError(name)
    return value


def _count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 1_000_000:
        raise ValueError(name)
    return value


def _cents(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 99:
        raise ValueError("limit_cents")
    return value


def client_order_id_for(intent_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, intent_id))


class _Closing:
    """Close the sqlite connection without hiding the caller's exception.

    isolation_level=None autocommits, so ROLLBACK after an explicit COMMIT
    raises. A raise after COMMIT must still surface the original error.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except sqlite3.OperationalError:
            pass
        self._conn.close()
        return False


class OrderMachine:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS intents (
                  intent_id TEXT PRIMARY KEY,
                  client_order_id TEXT NOT NULL UNIQUE,
                  venue TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  account_label TEXT NOT NULL,
                  decision_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  parent_intent_id TEXT,
                  ticker TEXT,
                  side TEXT,
                  count INTEGER NOT NULL,
                  limit_cents INTEGER,
                  fee_text TEXT,
                  host TEXT,
                  state TEXT NOT NULL,
                  venue_order_id TEXT,
                  filled_count INTEGER,
                  caller TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(venue, mode, account_label, decision_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  intent_id TEXT,
                  at TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS blocks (
                  account_key TEXT PRIMARY KEY,
                  reason TEXT NOT NULL,
                  at TEXT NOT NULL
                );
                """
            )

    def _conn(self) -> _Closing:
        conn = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute("PRAGMA journal_mode=WAL")
        return _Closing(conn)

    def _account_key(self, venue: str, mode: str, account: str) -> str:
        return f"{venue}|{mode}|{account}"

    def _blocked(self, conn, venue, mode, account) -> str | None:
        row = conn.execute(
            "SELECT reason FROM blocks WHERE account_key=?",
            (self._account_key(venue, mode, account),)).fetchone()
        return None if row is None else row["reason"]

    def _block(self, conn, venue, mode, account, reason: str) -> None:
        conn.execute(
            "INSERT INTO blocks(account_key, reason, at) VALUES(?,?,?) "
            "ON CONFLICT(account_key) DO UPDATE SET reason=excluded.reason, at=excluded.at",
            (self._account_key(venue, mode, account), reason, _now()))
        self._event(conn, None, "blocked", {"reason": reason, "account_label": account})

    def _clear_block(self, conn, venue, mode, account) -> None:
        conn.execute("DELETE FROM blocks WHERE account_key=?",
                     (self._account_key(venue, mode, account),))

    def _event(self, conn, intent_id, kind: str, detail: dict) -> None:
        conn.execute(
            "INSERT INTO events(intent_id, at, kind, detail) VALUES(?,?,?,?)",
            (intent_id, _now(), kind, json.dumps(detail, sort_keys=True)))

    def _check_venue(self, venue: str, mode: str, base_url: str | None) -> str | None:
        venue = _token(venue, "venue")
        mode = _token(mode, "mode")
        if venue == "kalshi_demo" and mode == "demo":
            if not base_url:
                raise ProductionRefused("host '' is not a Kalshi DEMO host - refused")
            return assert_demo_base(base_url)
        if venue == "polymarket_paper" and mode == "paper":
            if base_url:
                raise NoRealPolymarketOrderApi("Polymarket paper candidate has no order API")
            return None
        raise ValueError("venue")

    def submit(self, transport, *, venue: str, mode: str, account_label: str, decision_id: str,
               ticker: str, side: str, count: int, limit_cents: int, caller: str,
               base_url: str | None = None, fee_text: str | None = None,
               role: str = "parent", parent_intent_id: str | None = None) -> dict:
        host = self._check_venue(venue, mode, base_url)
        account_label = _token(account_label, "account_label")
        decision_id = _token(decision_id, "decision_id")
        if len(decision_id) > 80:
            raise ValueError("decision_id")
        ticker = _token(ticker, "ticker")
        side = _token(side, "side")
        caller = _token(caller, "caller")
        count = _count(count, "count")
        limit_cents = _cents(limit_cents)
        if fee_text is not None and (not isinstance(fee_text, str) or len(fee_text) > 32 or any(ch not in "0123456789." for ch in fee_text)):
            raise ValueError("fee_text")
        intent_id = f"{venue}.{mode}.{account_label}.{decision_id}"
        client_order_id = client_order_id_for(intent_id)
        owned = False
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self._blocked(conn, venue, mode, account_label):
                    existing = conn.execute(
                        "SELECT intent_id FROM intents WHERE venue=? AND mode=? AND account_label=? AND decision_id=?",
                        (venue, mode, account_label, decision_id)).fetchone()
                    conn.execute("COMMIT")
                    if existing is None:
                        raise SubmitBlocked("account is blocked pending reconcile")
                    owned = False
                    intent_id = existing["intent_id"]
                    return_existing = True
                else:
                    return_existing = False
                if not return_existing and role == "resting_child":
                    parent = conn.execute(
                        "SELECT * FROM intents WHERE intent_id=?", (parent_intent_id,)).fetchone()
                    if parent is None or parent["filled_count"] is None:
                        conn.execute("COMMIT")
                        raise SubmitBlocked("resting child requires a known partial fill")
                    if count != parent["count"] - parent["filled_count"]:
                        conn.execute("COMMIT")
                        raise SubmitBlocked("resting child count is not the parent remainder")
                if not return_existing:
                    conn.execute(
                        """INSERT OR IGNORE INTO intents(
                            intent_id, client_order_id, venue, mode, account_label, decision_id, role,
                            parent_intent_id, ticker, side, count, limit_cents, fee_text, host, state,
                            caller, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'recorded', ?, ?, ?)""",
                        (intent_id, client_order_id, venue, mode, account_label, decision_id, role,
                         parent_intent_id, ticker, side, count, limit_cents, fee_text, host, caller,
                         _now(), _now()))
                    row = conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
                    if row["count"] != count or row["limit_cents"] != limit_cents or row["ticker"] != ticker or row["side"] != side:
                        conn.execute("COMMIT")
                        raise SubmitBlocked("existing intent does not match this order")
                    if row["state"] == "recorded":
                        cur = conn.execute(
                            "UPDATE intents SET state='submitting', updated_at=? WHERE intent_id=? AND state='recorded'",
                            (_now(), intent_id))
                        owned = cur.rowcount == 1
                        if owned:
                            self._event(conn, intent_id, "submitting", {"client_order_id": client_order_id, "caller": caller})
                    elif row["state"] == "submitting":
                        conn.execute(
                            "UPDATE intents SET state='submit_unknown', updated_at=? WHERE intent_id=? AND state='submitting'",
                            (_now(), intent_id))
                        self._event(conn, intent_id, "submit_unknown", {"reason": "restart_during_submit"})
                        self._block(conn, venue, mode, account_label, "submit outcome unknown")
                    conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        if not owned:
            return self._public(intent_id, submitted=False)
        try:
            result = transport({
                "client_order_id": client_order_id,
                "ticker": ticker,
                "side": side,
                "count": count,
                "limit_cents": limit_cents,
                "role": role,
            })
        except Exception as exc:
            self._finish_unknown(intent_id, type(exc).__name__)
            return self._public(intent_id, submitted=True)
        self._apply_transport(intent_id, result if isinstance(result, dict) else {})
        return self._public(intent_id, submitted=True)

    def _finish_unknown(self, intent_id: str, exc_name: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE intents SET state='submit_unknown', updated_at=? "
                "WHERE intent_id=? AND state IN ('submitting','submit_unknown')",
                (_now(), intent_id))
            row = conn.execute("SELECT venue, mode, account_label FROM intents WHERE intent_id=?",
                               (intent_id,)).fetchone()
            if row is not None:
                self._block(conn, row["venue"], row["mode"], row["account_label"], "submit outcome unknown")
            self._event(conn, intent_id, "submit_unknown", {"exception_type": exc_name})
            conn.execute("COMMIT")

    def _apply_transport(self, intent_id: str, result: dict) -> None:
        outcome = result.get("outcome")
        order_id = result.get("order_id") if isinstance(result.get("order_id"), str) and len(result.get("order_id")) <= 80 else None
        fill = result.get("fill_count")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None or row["state"] not in ("submitting", "submit_unknown"):
                conn.execute("COMMIT")
                return
            if outcome == "reject":
                state, filled = "rejected", None
            elif outcome == "ack" and order_id:
                if isinstance(fill, bool) or not isinstance(fill, int):
                    state, filled = "ack_unknown", None
                elif fill < 0 or fill > row["count"]:
                    self._block(conn, row["venue"], row["mode"], row["account_label"], "fill_count outside order")
                    state, filled = "ack_unknown", None
                elif fill == row["count"]:
                    state, filled = "filled", fill
                else:
                    state, filled = "partial", fill
            else:
                state, filled = "submit_unknown", None
            conn.execute(
                "UPDATE intents SET state=?, venue_order_id=?, filled_count=?, updated_at=? WHERE intent_id=?",
                (state, order_id, filled, _now(), intent_id))
            if state in {"submit_unknown", "ack_unknown"}:
                self._block(conn, row["venue"], row["mode"], row["account_label"], "submit outcome unknown")
            self._event(conn, intent_id, state, {"order_id": order_id, "fill_count": filled})
            conn.execute("COMMIT")

    def open_resting_child(self, transport, *, parent_intent_id: str, account_label: str,
                           venue: str = "kalshi_demo", mode: str = "demo", base_url: str,
                           caller: str) -> dict:
        """One resting remainder. Its count is the parent remainder, not a new size."""
        parent_intent_id = _token(parent_intent_id, "parent") if len(parent_intent_id) <= 96 else parent_intent_id
        if not isinstance(parent_intent_id, str) or not parent_intent_id or len(parent_intent_id) > 240:
            raise ValueError("parent")
        if any(ch not in _TOKEN_OK for ch in parent_intent_id) or parent_intent_id.lower() in {"unknown", "live"}:
            raise ValueError("parent")
        with self._conn() as conn:
            parent = conn.execute("SELECT * FROM intents WHERE intent_id=?", (parent_intent_id,)).fetchone()
        if parent is None or parent["state"] != "partial" or parent["filled_count"] is None:
            raise SubmitBlocked("resting child requires a known partial fill")
        remainder = parent["count"] - parent["filled_count"]
        if remainder < 1:
            raise SubmitBlocked("nothing left to rest")
        child_id = parent["decision_id"] + ".rest"
        return self.submit(
            transport, venue=venue, mode=mode, account_label=account_label, decision_id=child_id,
            ticker=parent["ticker"], side=parent["side"], count=remainder, limit_cents=parent["limit_cents"],
            caller=caller, base_url=base_url, role="resting_child", parent_intent_id=parent["intent_id"])

    def request_cancel(self, transport, *, intent_id: str, account_label: str, venue: str, mode: str,
                       base_url: str | None = None) -> dict:
        """Record the cancel attempt. A transport result is not proof of cancellation."""
        self._check_venue(venue, mode, base_url)
        if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 240:
            raise ValueError("intent")
        if any(ch not in _TOKEN_OK for ch in intent_id) or intent_id.lower() in {"unknown", "live"}:
            raise ValueError("intent")
        account_label = _token(account_label, "account_label")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None or row["account_label"] != account_label or row["venue"] != venue or row["mode"] != mode:
                conn.execute("COMMIT")
                raise SubmitBlocked("cancel crosscheck failed")
            if row["state"] in {"recorded", "rejected"}:
                conn.execute("COMMIT")
                raise SubmitBlocked("nothing submitted to cancel")
            conn.execute(
                "UPDATE intents SET state='cancel_requested', updated_at=? WHERE intent_id=?",
                (_now(), intent_id))
            self._event(conn, intent_id, "cancel_requested", {})
            client_order_id = row["client_order_id"]
            venue_order_id = row["venue_order_id"]
            conn.execute("COMMIT")
        try:
            transport({"client_order_id": client_order_id, "venue_order_id": venue_order_id})
        except Exception as exc:
            exc_name = type(exc).__name__
        else:
            exc_name = None
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE intents SET state='cancel_unknown', updated_at=? WHERE intent_id=? AND state='cancel_requested'",
                (_now(), intent_id))
            self._event(conn, intent_id, "cancel_unknown", {"exception_type": exc_name})
            self._block(conn, venue, mode, account_label, "cancel outcome unknown")
            conn.execute("COMMIT")
        return self._public(intent_id, submitted=False)

    def reconcile(self, snapshot: dict, *, venue: str, mode: str, account_label: str,
                  base_url: str | None = None) -> dict:
        """Apply an explicit venue snapshot. Disagreement blocks submits."""
        self._check_venue(venue, mode, base_url)
        account_label = _token(account_label, "account_label")
        orders = snapshot.get("orders") if isinstance(snapshot, dict) else None
        if not isinstance(orders, list):
            raise ValueError("snapshot")
        parsed = []
        for item in orders:
            if not isinstance(item, dict):
                raise ValueError("snapshot")
            cid = item.get("client_order_id")
            status = item.get("status")
            if not isinstance(cid, str) or not isinstance(status, str):
                raise ValueError("snapshot")
            filled = item.get("filled_count")
            if filled is not None and (isinstance(filled, bool) or not isinstance(filled, int)):
                raise ValueError("snapshot")
            parsed.append((cid, status, filled, item.get("order_id") if isinstance(item.get("order_id"), str) else None))
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM intents WHERE venue=? AND mode=? AND account_label=?",
                (venue, mode, account_label))]
            by_client = {row["client_order_id"]: row for row in rows}
            host = assert_demo_base(base_url) if venue == "kalshi_demo" else None
            problems = []
            for row in rows:
                if row["host"] and host and row["host"] != host:
                    problems.append("recovery host does not match the intent")
            updates = []
            seen = set()
            for cid, status, filled, order_id in parsed:
                row = by_client.get(cid)
                if row is None:
                    problems.append("venue order has no local intent")
                    continue
                seen.add(cid)
                if row["host"] and venue == "kalshi_demo":
                    pass
                if filled is not None and (filled < 0 or filled > row["count"]):
                    problems.append("snapshot fill outside order")
                    continue
                if row["filled_count"] is not None and filled is not None and row["filled_count"] != filled:
                    problems.append("snapshot fill disagrees with local fill")
                    continue
                if status in _EXPLICIT_CANCEL:
                    if filled is None:
                        problems.append("cancel without an explicit fill count")
                        continue
                    updates.append((row["intent_id"], "cancelled", filled, order_id or row["venue_order_id"]))
                elif status in _EXPLICIT_FILL:
                    if filled is None:
                        problems.append("fill without an explicit fill count")
                        continue
                    new_state = "filled" if filled == row["count"] else "partial"
                    updates.append((row["intent_id"], new_state, filled, order_id or row["venue_order_id"]))
                elif status in _RESTING:
                    if filled is None:
                        problems.append("resting order without an explicit fill count")
                        continue
                    new_state = "filled" if filled == row["count"] else "partial"
                    updates.append((row["intent_id"], new_state, filled, order_id or row["venue_order_id"]))
                else:
                    problems.append("snapshot status is not explicit")
            for row in rows:
                # Absence never becomes filled or cancelled. An order that is
                # already in one of those states can be omitted; every other
                # submitted intent must still be present or the account blocks.
                if row["client_order_id"] in seen or row["state"] in {"filled", "cancelled", "rejected", "recorded"}:
                    continue
                if row["state"] in _SUBMITTED:
                    problems.append("submitted intent missing from snapshot")
            if problems:
                self._block(conn, venue, mode, account_label, problems[0])
                conn.execute("COMMIT")
                return {"ok": False, "blocked": True, "reason": problems[0]}
            for intent_id, state, filled, order_id in updates:
                conn.execute(
                    "UPDATE intents SET state=?, filled_count=?, venue_order_id=?, updated_at=? WHERE intent_id=?",
                    (state, filled, order_id, _now(), intent_id))
                self._event(conn, intent_id, "reconciled", {"state": state, "filled_count": filled})
            self._clear_block(conn, venue, mode, account_label)
            conn.execute("COMMIT")
        return {"ok": True, "blocked": False, "reason": None}

    def _public(self, intent_id: str, *, submitted: bool) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        out = dict(row)
        out["submitted_this_call"] = submitted
        return out

    def list_intents(self, *, venue: str, mode: str, account_label: str) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM intents WHERE venue=? AND mode=? AND account_label=? ORDER BY created_at",
                (venue, mode, account_label))]

    def is_blocked(self, *, venue: str, mode: str, account_label: str) -> bool:
        with self._conn() as conn:
            return self._blocked(conn, venue, mode, account_label) is not None
