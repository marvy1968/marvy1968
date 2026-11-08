"""The Odds API (primary, credit-billed). Wraps ajbet.oddsapi.OddsAPI, which keeps its own
key-aware BudgetGuard (paid key -> free key fallback on 401/429, per-strategy allocations)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..oddsapi import OddsAPI
from .base import Provider


class TheOddsAPIProvider(Provider):
    name = "theoddsapi"
    auth_env = "ODDS_API_KEY_PAID"      # informational; OddsAPI reads odds_api.keys from config

    def __init__(self, cfg, db, pcfg=None, client: Optional[OddsAPI] = None):
        super().__init__(cfg, db, pcfg)
        self.client = client or OddsAPI(cfg, db)

    @property
    def available(self) -> bool:
        return self.enabled and self.client.has_key

    @property
    def calls(self) -> int:              # type: ignore[override]
        return self.client.calls

    @calls.setter
    def calls(self, v) -> None:
        pass

    def _tag(self, evs):
        for e in evs or []:
            e.setdefault("_provider", self.name)
        return evs

    def events(self, sport_key, strategy="value"):
        return self.client.events(sport_key, strategy)

    def odds(self, sport_key, markets, strategy="value"):
        return self._tag(self.client.live_odds(sport_key, strategy=strategy, markets=markets))

    def event_odds(self, sport_key, event_id, markets, strategy="value"):
        ev = self.client.event_odds(sport_key, event_id, markets, strategy=strategy)
        return self._tag([ev])[0] if ev else ev

    def status(self):
        s = super().status()
        s["key_label"] = self.client.key_label
        return s
