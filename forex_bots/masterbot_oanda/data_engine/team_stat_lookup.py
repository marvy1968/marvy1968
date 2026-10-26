from __future__ import annotations
import json
import os
import re
from typing import Any

CACHE_PATH = os.path.join(os.path.dirname(__file__), "team_stats_cache.json")

LEAGUE_AVG_PROFILE: dict[str, float] = {
    "off_3pa_rate": 0.38,
    "off_3p_pct": 0.34,
    "off_2p_pct": 0.51,
    "off_ft_rate": 0.20,
    "off_ft_pct": 0.71,
    "off_tov_rate": 0.17,
    "off_orb_rate": 0.30,
    "def_3pa_rate_allowed": 0.38,
    "def_3p_pct_allowed": 0.34,
    "def_2p_pct_allowed": 0.51,
    "def_ft_rate_allowed": 0.20,
    "def_tov_forced_rate": 0.17,
    "def_drb_rate": 0.70,
}

TEAM_ALIASES = {
    "unc": "north carolina",
    "tar heels": "north carolina",
    "uconn": "connecticut",
    "ole miss": "mississippi",
    "msu": "michigan state",
    "usu": "utah state",
    "lsu": "louisiana state",
    "pitt": "pittsburgh",
}

def normalize_team_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return TEAM_ALIASES.get(name, name)

def load_team_stats_cache() -> dict[str, Any]:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def get_team_stat_profile(team_name: str) -> dict[str, float]:
    cache = load_team_stats_cache()
    norm = normalize_team_name(team_name)

    if norm in cache:
        return {**LEAGUE_AVG_PROFILE, **cache[norm]}

    for raw_key, profile in cache.items():
        if normalize_team_name(raw_key) == norm:
            return {**LEAGUE_AVG_PROFILE, **profile}

    return LEAGUE_AVG_PROFILE.copy()
