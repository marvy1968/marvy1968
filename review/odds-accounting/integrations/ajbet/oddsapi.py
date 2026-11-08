"""The Odds API client (live odds only) with a hard credit budget guard.

Never calls /historical (paid plan only). The API key is read from the environment at
runtime and is scrubbed from every log line / exception message.
"""
from __future__ import annotations

import contextvars
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from .db import DB, alloc_daily, is_weekend_local, month_utc, now, today_local

log = logging.getLogger(__name__)

# transport(url, params) -> (status_code, headers, json_body)
Transport = Callable[[str, Dict[str, Any]], Tuple[int, Dict[str, str], Any]]


class BudgetExceeded(Exception):
    pass


class BudgetGuard:
    """Hard guard shared by all strategies.

    1. Per-key floor: refuse any call that would drop that key's remaining credits below its floor
       (paid key ~500, free key 50).
    2. Global daily cap (CT day).
    3. Per-strategy allocations, daily (CT) and monthly (UTC month, matching the Odds API reset), so
       Strategy C ('value') cannot starve A/B ('live') and vice versa.
    Remaining comes from the key's last x-requests-remaining header (stale after the UTC month rolls)."""

    def __init__(self, db: DB, stop_at_remaining: float = 50, daily_cap: Optional[float] = None,
                 plan_credits: float = 500, allocations: Optional[Dict[str, Dict[str, float]]] = None,
                 key_limits: Optional[Dict[str, Dict[str, float]]] = None,
                 daily_cap_weekend: Optional[float] = None):
        self.db = db
        self.daily_cap_weekend = daily_cap_weekend   # Sat/Sun (CT) global cap; None = same as daily_cap
        self.stop_at = stop_at_remaining
        self.daily_cap = daily_cap
        self.plan_credits = plan_credits
        self.allocations = allocations or {}
        self.key_limits = key_limits or {}

    def _limits(self, key_label: Optional[str]) -> Tuple[float, float]:
        kl = self.key_limits.get(key_label or "", {})
        return float(kl.get("floor", self.stop_at)), float(kl.get("plan_credits", self.plan_credits))

    def remaining(self, key_label: Optional[str] = None) -> Optional[float]:
        row = self.db.last_usage(key_label if self.key_limits else None)
        if row is None:
            return None
        ts_utc = row["ts_utc"] if "ts_utc" in row.keys() and row["ts_utc"] else None
        month = ts_utc[:7] if ts_utc else row["ts"][:7]
        if month != month_utc():   # quota reset on the 1st 00:00 UTC
            return None
        return float(row["remaining"])

    def check(self, cost: float, strategy: str = "live", key_label: Optional[str] = None) -> Tuple[bool, str]:
        floor, plan = self._limits(key_label)
        rem = self.remaining(key_label)
        known = rem if rem is not None else plan
        if known - cost < floor:
            return False, f"budget guard[{key_label or 'key'}]: remaining {known:.0f} - cost {cost:.0f} < floor {floor:.0f}"
        today = today_local()
        cap = self.daily_cap
        if cap is not None and self.daily_cap_weekend is not None and is_weekend_local(today):
            cap = self.daily_cap_weekend
        try:   # 2026-10-24 (Marvin): credit_plan daily cap to the reset / Saturday cap (ajbet/sat_priority.py)
            from . import sat_priority as _sp
            cap = _sp.day_cap(today, cap)
        except Exception:  # noqa: BLE001
            pass
        if cap is not None:
            used = self.db.credits_used_on(today)
            if used + cost > cap:
                return False, f"daily cap: used {used:.0f} + {cost:.0f} > {cap}"
        alloc = self.allocations.get(strategy) or {}
        daily = alloc_daily(alloc, today)
        if daily is not None:
            used = self.db.credits_used_on(today, strategy)
            if used + cost > daily:
                return False, f"{strategy} daily allocation: used {used:.0f} + {cost:.0f} > {daily}"
        if alloc.get("monthly") is not None:
            used = self.db.credits_used_in_month(month_utc(), strategy)
            if used + cost > alloc["monthly"]:
                return False, f"{strategy} monthly allocation: used {used:.0f} + {cost:.0f} > {alloc['monthly']}"
        return True, "ok"


# Hops from the real Session only. A test double that returns a tuple does not
# set this. requests follows redirects inside one get; urllib3 status retries
# stay at the Session default of 0. No sleep is added here.
_HOPS: contextvars.ContextVar = contextvars.ContextVar("aj_odds_hops", default=None)


def _remember_hops(response) -> None:
    hops = []
    for hop in list(getattr(response, "history", None) or ()) + [response]:
        hops.append((getattr(hop, "status_code", None), getattr(hop, "headers", None)))
    _HOPS.set(hops)


def _take_hops():
    hops = _HOPS.get()
    _HOPS.set(None)
    return hops


def requests_transport(timeout: float = 20.0) -> Transport:
    s = requests.Session()

    def _t(url: str, params: Dict[str, Any]):
        _HOPS.set(None)
        r = s.get(url, params=params, timeout=timeout)
        try:
            body = r.json()
        except ValueError:
            body = None
        _remember_hops(r)
        return r.status_code, dict(r.headers), body
    return _t


def _hnum(headers: Dict[str, str], name: str) -> Optional[float]:
    for k, v in headers.items():
        if k.lower() == name:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


_AJ_STRATEGIES = {
    "live", "live_props", "value", "team_props", "props_nba", "props_ncaab",
    "props_mlb", "props_nhl", "props_soccer", "close", "query",
}


def _public_scope(params: Dict[str, Any]) -> Dict[str, Any]:
    """Non-secret request shape. The api key and the raw query are not copied."""
    def split(name: str):
        raw = params.get(name) if isinstance(params, dict) else None
        if not isinstance(raw, str) or raw == "":
            return None
        return [part for part in raw.split(",") if part]

    scope: Dict[str, Any] = {
        "markets": split("markets"),
        "regions": split("regions"),
        "bookmakers": split("bookmakers"),
    }
    odds_format = params.get("oddsFormat") if isinstance(params, dict) else None
    if isinstance(odds_format, str):
        scope["odds_format"] = odds_format
    return scope


def _accounting_note(**kwargs) -> None:
    """Observe-only. A failure here must not change the Odds API result."""
    try:
        from odds_accounting.boundary import observe_exchange
        strategy = kwargs.get("strategy")
        kwargs["source_job"] = f"aj-{strategy}" if strategy in _AJ_STRATEGIES else "aj-odds"
        observe_exchange(**kwargs)
    except Exception:
        return


class OddsAPI:
    def __init__(self, cfg: Dict[str, Any], db: DB, transport: Optional[Transport] = None,
                 api_key: Any = None):
        oc = cfg.get("odds_api", {})
        from . import sat_priority
        sat_priority.configure(cfg)
        self.base = oc.get("base_url", "https://api.the-odds-api.com/v4").rstrip("/")
        self.regions = oc.get("regions", "us")
        # Up to 10 explicit bookmakers (any region) bill as ONE region, which lets us add
        # Pinnacle (eu) as the sharp reference at no extra cost.
        books = oc.get("bookmakers") or []
        self.bookmakers = ",".join(books) if isinstance(books, list) else str(books)
        self.markets = oc.get("markets", "h2h,spreads,totals")
        self.odds_format = oc.get("odds_format", "american")
        self.cooldown = float(oc.get("sport_cooldown_s", 300))
        # Keys are read from env vars at runtime only (never written anywhere). Order = priority:
        # paid first; the next key is used only when the current one answers 401/429.
        key_cfg = oc.get("keys") or [{"label": "free", "env": oc.get("key_env", "ODDS_API_KEY")}]
        if isinstance(api_key, list):          # tests: [{"label":..., "key":...}, ...]
            self._keys = api_key
        elif api_key is not None:
            self._keys = [{"label": key_cfg[0].get("label", "free"), "key": api_key}] if api_key else []
        else:
            self._keys = [{"label": k.get("label", k["env"]), "key": os.environ.get(k["env"], "")} for k in key_cfg]
            self._keys = [k for k in self._keys if k["key"]]
        self._active = 0
        self._disabled_until: Dict[str, datetime] = {}
        self.fallback_cooldown = timedelta(seconds=float(oc.get("fallback_retry_s", 900)))
        self.db = db
        key_limits = {k.get("label", k.get("env")): {"floor": k.get("floor", oc.get("stop_at_remaining", 50)),
                                                     "plan_credits": k.get("plan_credits", oc.get("monthly_plan_credits", 500))}
                      for k in key_cfg}
        self.guard = BudgetGuard(db, oc.get("stop_at_remaining", 50), oc.get("daily_credit_cap"),
                                 oc.get("monthly_plan_credits", 500), oc.get("allocations"), key_limits,
                                 oc.get("daily_credit_cap_weekend"))
        self.transport = transport or requests_transport()
        self.calls = 0

    @property
    def has_key(self) -> bool:
        return bool(self._keys)

    @property
    def key_label(self) -> Optional[str]:
        k = self._current()
        return k["label"] if k else None

    def _current(self) -> Optional[Dict[str, str]]:
        t = now()
        for i, k in enumerate(self._keys):
            until = self._disabled_until.get(k["label"])
            if until is None or t >= until:
                return k
        return None

    def _scrub(self, s: str) -> str:
        for k in self._keys:
            if k["key"]:
                s = s.replace(k["key"], "***")
        return s

    def region_units(self) -> int:
        if self.bookmakers:
            n = len([b for b in self.bookmakers.split(",") if b])
            return max(1, -(-n // 10))
        return len([r for r in self.regions.split(",") if r])

    def _book_params(self) -> Dict[str, str]:
        return {"bookmakers": self.bookmakers} if self.bookmakers else {"regions": self.regions}

    @staticmethod
    def cost_of(markets: str, regions: str) -> int:
        return len([m for m in markets.split(",") if m]) * len([r for r in regions.split(",") if r])

    def estimate(self, markets: str) -> int:
        """Upper-bound cost (billing is per unique market RETURNED x regions)."""
        return len([m for m in markets.split(",") if m]) * self.region_units()

    def cooldown_ok(self, sport_key: str) -> bool:
        last = self.db.sport_last_call(sport_key)
        if not last:
            return True
        return now() - datetime.fromisoformat(last) >= timedelta(seconds=self.cooldown)

    def _get(self, path: str, params: Dict[str, Any], cost: int, sport: str, label: str,
             strategy: str = "live") -> Optional[Any]:
        if "historical" in path:
            _accounting_note(
                endpoint_path=path, estimated_cost=None, estimate_provenance="historical_refused",
                strategy=strategy, preflight="historical_disabled", **_public_scope(params))
            raise ValueError("historical endpoints are not used (backtests are a separate, explicit job)")
        logical_request_id = uuid.uuid4().hex
        ordinal = 0
        scope = _public_scope(params)
        provenance = ("documented_free_events" if path.endswith("/events") and cost == 0
                      else "aj_estimate_markets_times_region_units")
        while True:
            k = self._current()
            if k is None:
                log.warning("No usable Odds API key (unset or all disabled); skipping %s", label)
                return None
            if cost > 0:
                ok, why = self.guard.check(cost, strategy, k["label"])
                if not ok:
                    log.warning("Odds API %s %s skipped: %s", label, sport, why)
                    self.db.log_usage(label, sport, 0, 0, None, None, "SKIPPED " + why, strategy, k["label"])
                    _accounting_note(
                        endpoint_path=path, estimated_cost=cost, estimate_provenance=provenance,
                        strategy=strategy, account_label=k["label"], preflight="budget",
                        logical_request_id=logical_request_id, **scope)
                    raise BudgetExceeded(why)
            q = dict(params, apiKey=k["key"])
            try:
                status, headers, body = self.transport(f"{self.base}{path}", q)
            except Exception as e:  # never leak a key through exception text
                msg = self._scrub(str(e))
                _HOPS.set(None)
                ordinal += 1
                _accounting_note(
                    endpoint_path=path, estimated_cost=cost, estimate_provenance=provenance,
                    strategy=strategy, account_label=k["label"], transport_error=e,
                    logical_request_id=logical_request_id, attempt_id=uuid.uuid4().hex,
                    attempt_ordinal=ordinal, **scope)
                log.error("Odds API %s %s error: %s", label, sport, msg)
                self.db.log_usage(label, sport, -1, None, None, None, "ERROR " + msg[:200], strategy, k["label"])
                return None
            self.calls += 1
            rem, used, last = (_hnum(headers, "x-requests-remaining"), _hnum(headers, "x-requests-used"),
                               _hnum(headers, "x-requests-last"))
            self.db.log_usage(label, sport, status, last if last is not None else (cost if status == 200 else 0),
                              rem, used, "" if status == 200 else self._scrub(str(body))[:200], strategy, k["label"])
            hops = _take_hops() or [(status, headers)]
            for hop_status, hop_headers in hops:
                ordinal += 1
                _accounting_note(
                    endpoint_path=path, estimated_cost=cost, estimate_provenance=provenance,
                    strategy=strategy, account_label=k["label"], http_status=hop_status, headers=hop_headers,
                    logical_request_id=logical_request_id, attempt_id=uuid.uuid4().hex,
                    attempt_ordinal=ordinal, **scope)
            log.info("Odds API %s %s [%s/%s] -> %s | est=%s last=%s remaining=%s used=%s",
                     label, sport, strategy, k["label"], status, cost, last, rem, used)
            if status in (401, 429) and k is not self._keys[-1]:
                # 401 (invalid/deactivated) -> disable for this process; 429 -> retry later
                until = now() + (timedelta(days=3650) if status == 401 else self.fallback_cooldown)
                self._disabled_until[k["label"]] = until
                log.warning("Odds API key '%s' returned %s; falling back to next key", k["label"], status)
                continue
            if status != 200:
                return None
            return body

    def live_odds(self, sport_key: str, strategy: str = "live", markets: Optional[str] = None) -> Optional[list]:
        """One call per sport: featured markets h2h,spreads,totals (3 credits with 1 region unit)."""
        markets = markets or self.markets
        params = dict(self._book_params(), markets=markets, oddsFormat=self.odds_format)
        body = self._get(f"/sports/{sport_key}/odds", params, self.estimate(markets), sport_key, "odds", strategy)
        self.db.set_sport_last_call(sport_key)
        return body

    def event_odds(self, sport_key: str, event_id: str, markets: str = "alternate_totals",
                   strategy: str = "live") -> Optional[dict]:
        params = dict(self._book_params(), markets=markets, oddsFormat=self.odds_format)
        return self._get(f"/sports/{sport_key}/events/{event_id}/odds", params, self.estimate(markets),
                         sport_key, f"event_odds:{markets}", strategy)

    def events(self, sport_key: str, strategy: str = "value") -> Optional[list]:
        """/events is free (0 credits): event ids + teams + commence times."""
        return self._get(f"/sports/{sport_key}/events", {}, 0, sport_key, "events", strategy)
