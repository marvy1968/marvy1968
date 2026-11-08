"""NBA player props on Telegram (Marvin 2026-10-24): pregame + in-game NBA PLAYER props (player_* markets) alert
like other sports' props (pregame ⭐⭐⭐ PROP_MIN_STARS, live ⭐⭐ LIVE_PROP_MIN_STARS, Bovada price, normal
prop_min_books, same dedupe). NBA game lines / comeback / team totals keep the NBA paper-only rules
(watchlist.paper_only_sports, telegram_exclude_sports, alerts.league_allow). Alerts only; Bovada is manual.
Off: NBA_PROPS_TG=off | data/NBA_PROPS_TG_OFF | /opt/marv-feed/NBA_PROPS_TG_OFF
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def enabled() -> bool:
    if os.environ.get("NBA_PROPS_TG", "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not ((_ROOT / "data" / "NBA_PROPS_TG_OFF").exists() or Path("/opt/marv-feed/NBA_PROPS_TG_OFF").exists())


def is_nba_player_prop(f) -> bool:
    return (getattr(f, "sport", None) == "nba" and bool(getattr(f, "player", None))
            and str(getattr(f, "market", "")).startswith("player_") and enabled())
 


def _sw(env: str, name: str) -> bool:
    if os.environ.get(env, "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not ((_ROOT / "data" / name).exists() or Path("/opt/marv-feed", name).exists())


def gamelines_blocked() -> bool:
    """2026-10-24 bug fix: every NBA alert that is not a player prop (line swing, comeback, ...) stays paper only.
    Off: NBA_GAMELINES_TG_BLOCK=off | data/ or /opt/marv-feed/NBA_GAMELINES_TG_BLOCK_OFF"""
    return _sw("NBA_GAMELINES_TG_BLOCK", "NBA_GAMELINES_TG_BLOCK_OFF")


def need_bovada_on() -> bool:
    """2026-10-24 bug fix: a live line swing with no Bovada price ('check Bovada (no price yet)') is paper only.
    Off: SWING_NEED_BOVADA=off | data/ or /opt/marv-feed/SWING_NEED_BOVADA_OFF"""
    return _sw("SWING_NEED_BOVADA", "SWING_NEED_BOVADA_OFF")
