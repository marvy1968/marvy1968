"""Which bot sports use which stats module, and the pick rules each one earned in backtesting."""

from datetime import datetime
from pathlib import Path

from .live import StatsRules, project_slate

MODULE_NAMES = {"nfl": "nfl", "cfb": "ncaaf", "nba": "nba", "wnba": "wnba", "mlb": "mlb",
                "ncaab": "ncaab", "ncaaw": "ncaaw", "euroleague": "euroleague"}
MODULES = set(MODULE_NAMES)

# From the walk-forward backtests: thresholds tuned on early seasons, checked on later ones
# (ANALYSIS.md has the numbers behind each value).
RULES: dict[str, StatsRules] = {
    # NBA 2022-26 test: >=86% confidence -> 126 picks, 90.5%; O/U edge >=17% -> 52.8% (break-even)
    "nba": StatsRules(ml_min_prob=0.86, ou_min_edge=0.17, ou_enabled=True),
    # NFL 2022-25 test: >=78% -> 30 picks, 90.0%; O/U ~49-51% at every edge
    "nfl": StatsRules(ml_min_prob=0.78, ou_enabled=False),
    # WNBA 2022-25 test: >=85% -> ~83-88% depending on seasons; no historical totals to test O/U
    "wnba": StatsRules(ml_min_prob=0.85, ou_enabled=False),
    # MLB 2023-25 test: best tier (>=70%) -> 43 picks, 76.7%; baseball never reaches 90%. No O/U history.
    "mlb": StatsRules(ml_min_prob=0.70, ou_enabled=False),
    # NCAA basketball / EuroLeague: set from backtests (see ANALYSIS.md); no historical totals for O/U.
    "ncaab": StatsRules(ml_min_prob=0.90, ou_enabled=False),
    "ncaaw": StatsRules(ml_min_prob=0.90, ou_enabled=False),
    "euroleague": StatsRules(ml_min_prob=0.85, ou_enabled=False),
    # College: not backtestable without a CFBD key; NFL-like floor until `stats-backtest --sport cfb` runs
    "cfb": StatsRules(ml_min_prob=0.85, ou_enabled=False),
}


def module_for(key: str):
    import importlib
    name = MODULE_NAMES[key]
    return getattr(importlib.import_module(f"marv.stats.{name}"), name.upper())


def project(key: str, slate, settings, now: datetime, history=None) -> dict:
    module = module_for(key)
    if key == "cfb":
        module.api_key = settings.cfbd_api_key
    return project_slate(module, slate, Path(settings.state_dir) / "cache", now, RULES[key], history=history)
