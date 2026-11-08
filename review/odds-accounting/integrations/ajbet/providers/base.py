"""Provider interface + per-provider budget guard."""
from __future__ import annotations

import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..db import DB, month_utc, today_local

log = logging.getLogger(__name__)

BOOK_ALIASES = {"espnbet": "espnbet", "caesars": "williamhill_us", "williamhill": "williamhill_us",
                "betonline": "betonlineag", "pinnaclesports": "pinnacle", "hardrock": "hardrockbet",
                "betmgmus": "betmgm", "fanduelsportsbook": "fanduel", "draftkingssportsbook": "draftkings"}


def norm_book(key: str) -> str:
    k = re.sub(r"[^a-z0-9_]", "", str(key or "").lower())
    return BOOK_ALIASES.get(k, k)


class ProviderError(RuntimeError):
    pass


class ProviderBudgetExceeded(ProviderError):
    pass


@dataclass
class BudgetSpec:
    unit: str = "requests"               # 'requests' (count calls) or 'credits' (provider-reported cost)
    per_day: Optional[float] = None
    per_month: Optional[float] = None
    per_minute: Optional[int] = None     # rate limit
    floor: Optional[float] = None        # keep this many units in reserve (credits providers)


class ProviderBudget:
    """Each provider gets its own guard; usage is logged in the shared api_usage ledger under
    key_label 'provider:<name>' (The Odds API keeps its own key-aware BudgetGuard)."""

    def __init__(self, name: str, spec: BudgetSpec, db: DB):
        self.name, self.spec, self.db = name, spec, db
        self.label = f"provider:{name}"
        self._recent: deque = deque()

    def check(self, cost: float = 1.0) -> None:
        s = self.spec
        if s.per_minute:
            t = time.time()
            while self._recent and t - self._recent[0] > 60:
                self._recent.popleft()
            if len(self._recent) >= s.per_minute:
                raise ProviderBudgetExceeded(f"{self.name}: rate limit {s.per_minute}/min")
        if s.per_day is not None and self.db.credits_used_on(today_local(), key_label=self.label) + cost > s.per_day:
            raise ProviderBudgetExceeded(f"{self.name}: daily budget {s.per_day:g} reached")
        if s.per_month is not None and \
                self.db.credits_used_in_month(month_utc(), key_label=self.label) + cost > s.per_month:
            raise ProviderBudgetExceeded(f"{self.name}: monthly budget {s.per_month:g} reached")

    def record(self, endpoint: str, sport: str, status: int, cost: float = 1.0, note: str = "",
               strategy: str = "value") -> None:
        self._recent.append(time.time())
        self.db.log_usage(endpoint, sport, status, cost, None, None, note[:200], strategy, self.label)


class Provider:
    """Base class. Subclasses set `name` and implement what they support; unsupported methods
    return None (the hub then tries the next provider in the fallback chain)."""

    name = "base"
    auth_env: Optional[str] = None       # env var holding the key (never logged / printed)
    default_budget = BudgetSpec()
    capabilities = ("events", "odds", "props", "scores", "live_state")

    def __init__(self, cfg: Dict[str, Any], db: DB, pcfg: Optional[Dict[str, Any]] = None):
        self.cfg, self.db = cfg, db
        self.pcfg = pcfg or {}
        self.priority = int(self.pcfg.get("priority", 50))
        self.enabled = bool(self.pcfg.get("enabled", False))
        self.role = self.pcfg.get("role", "fallback")       # primary | merge | fallback
        self.auth_env = self.pcfg.get("auth_env", self.auth_env)
        b = dict(self.default_budget.__dict__)
        b.update(self.pcfg.get("budget") or {})
        self.budget = ProviderBudget(self.name, BudgetSpec(**b), db)
        self.calls = 0

    # -- auth (env only; never written anywhere) --
    @property
    def key(self) -> str:
        return os.environ.get(self.auth_env, "") if self.auth_env else ""

    @property
    def available(self) -> bool:
        return self.enabled and (not self.auth_env or bool(self.key))

    def scrub(self, s: str) -> str:
        k = self.key
        return s.replace(k, "***") if k else s

    # -- interface (normalised outputs) --
    def events(self, sport_key: str, strategy: str = "value") -> Optional[List[Dict[str, Any]]]:
        return None

    def odds(self, sport_key: str, markets: str, strategy: str = "value") -> Optional[List[Dict[str, Any]]]:
        return None

    def event_odds(self, sport_key: str, event_id: str, markets: str, strategy: str = "value") -> Optional[Dict[str, Any]]:
        return None

    def scores(self, sport_key: str) -> Optional[List[Dict[str, Any]]]:
        return None

    def live_state(self, sport_key: str) -> Optional[List[Dict[str, Any]]]:
        return None

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "enabled": self.enabled, "available": self.available, "role": self.role,
                "priority": self.priority, "auth_env": self.auth_env, "budget": self.budget.spec.__dict__}
