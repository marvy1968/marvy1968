#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import Any

import aiohttp
from dotenv import load_dotenv

load_dotenv()

SPORT_KEYS = [
    s.strip()
    for s in os.getenv("SPORT_KEYS", "basketball_ncaab,basketball_wncaab").split(",")
    if s.strip()
]

HTTP_TIMEOUT_SEC = int(os.getenv("HTTP_TIMEOUT_SEC", "25"))

ESPN_SCOREBOARD_URLS = {
    "basketball_ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=200",
    "basketball_wncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard?groups=50&limit=200",
    "basketball_nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "basketball_wnba": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
}


def normalize_team(name: str) -> str:
    if not name: return ""
    s = " ".join(str(name).strip().lower().split())
    s = s.replace(".", "").replace("&", "and").replace("'", "").replace("-", " ")
    
    # Common replacements
    repls = {
        "st ": "saint ", "st. ": "saint ", "76ers": "sixers", 
        "trail blazers": "blazers", "uconn": "connecticut", "tcu": "texas christian",
        "lsu": "louisiana state", "ole miss": "mississippi", "oklahoma st": "oklahoma state",
        "nc state": "north carolina state", "usc": "southern california"
    }
    for k, v in repls.items():
        s = s.replace(k, v)

    # MASCOT STRIPPER: Remove common ending words that cause mismatches
    mascots = [
        "sooners", "buffaloes", "bluejays", "mountaineers", "gamecocks", 
        "volunteers", "wolverines", "lobos", "blue devils", "cardinal", 
        "scarlet knights", "boilermakers", "wildcats", "huskies", "tar heels",
        "tigers", "bulldogs", "panthers", "cougars", "knights", "rams"
    ]
    words = s.split()
    if len(words) > 1 and words[-1] in mascots:
        s = " ".join(words[:-1])
    
    return s.strip()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _clock_to_min_sec(clock: str) -> tuple[int, int]:
    try:
        s = str(clock or "").strip()
        if not s:
            return 0, 0

        if ":" in s:
            mins, secs = s.split(":")
            return _to_int(mins, 0), _to_int(secs, 0)

        if "." in s:
            secs = int(float(s))
            return 0, max(0, secs)

        if s.isdigit():
            return 0, int(s)
    except Exception:
        pass

    return 0, 0


def _phase_fields(sport_key: str, period: int) -> tuple[str, str, str]:
    sport_key = str(sport_key or "").lower()

    if sport_key == "basketball_nba":
        if period <= 0:
            return "4q", "Q1", "pregame"
        if period <= 4:
            return "4q", f"Q{period}", f"quarter_{period}"
        return "4q", f"OT{period - 4}", "overtime"

    if period <= 0:
        return "2h", "1H", "pregame"
    if period == 1:
        return "2h", "1H", "first_half"
    if period == 2:
        return "2h", "2H", "second_half"
    return "2h", "OT", "overtime"


def _parse_event(event: dict[str, Any], sport_key: str) -> dict[str, Any] | None:
    competitions = event.get("competitions", []) or []
    if not competitions:
        return None

    comp = competitions[0]
    competitors = comp.get("competitors", []) or []

    home: dict[str, Any] = {}
    away: dict[str, Any] = {}

    for c in competitors:
        if c.get("homeAway") == "home":
            home = c
        elif c.get("homeAway") == "away":
            away = c

    if not home or not away:
        return None

    status_obj = comp.get("status", {}) or {}
    status_type = status_obj.get("type", {}) or {}

    display_clock = status_obj.get("displayClock", "0:00")
    minutes_left, seconds_left = _clock_to_min_sec(display_clock)
    period = _to_int(status_obj.get("period", 0), 0)
    completed = bool(status_type.get("completed", False))

    mode, half, phase_label = _phase_fields(sport_key, period)

    status_short = status_type.get("shortDetail", "") or ""
    status_detail = status_type.get("detail", "") or status_short

    return {
        "home_team": home.get("team", {}).get("displayName", ""),
        "away_team": away.get("team", {}).get("displayName", ""),
        "home_score": _to_int(home.get("score", 0), 0),
        "away_score": _to_int(away.get("score", 0), 0),
        "minutes_left": minutes_left,
        "seconds_left": seconds_left,
        "display_clock": display_clock,
        "mode": mode,
        "half": half,
        "period_label": half,
        "phase_label": phase_label,
        "completed": completed,
        "status": status_short,
        "status_detail": status_detail,
        "event_id": event.get("id", ""),
        "sport_key": sport_key,
        "period": period,
    }


async def _fetch_one_sport(session: aiohttp.ClientSession, sport_key: str) -> list[dict[str, Any]]:
    url = ESPN_SCOREBOARD_URLS.get(sport_key)
    if not url:
        return []

    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for event in data.get("events", []) or []:
        parsed = _parse_event(event, sport_key)
        if parsed:
            out.append(parsed)
    return out


async def fetch_all_scoreboard_games() -> list[dict[str, Any]]:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        all_games: list[dict[str, Any]] = []
        for sport_key in SPORT_KEYS:
            rows = await _fetch_one_sport(session, sport_key)
            all_games.extend(rows)
        return all_games


def _tokens(x: str) -> set[str]:
    return {t for t in normalize_team(x).split() if t and len(t) > 2}


def _side_score(a: str, b: str) -> float:
    a_norm = normalize_team(a)
    b_norm = normalize_team(b)

    if not a_norm or not b_norm:
        return 0.0

    if a_norm == b_norm:
        return 1.0

    ta = _tokens(a_norm)
    tb = _tokens(b_norm)

    if not ta or not tb:
        return 0.0

    overlap = len(ta & tb)
    union = len(ta | tb)
    score = overlap / union if union else 0.0

    if a_norm in b_norm or b_norm in a_norm:
        score += 0.15

    return min(score, 1.0)



def _clean_team_name(name):
    if not name: return ""
    name = str(name).strip().lower()
    # Remove common mascots and extra words
    parts = name.split()
    if len(parts) > 1:
        if parts[0] in ['north', 'south', 'west', 'east', 'new']:
            return " ".join(parts[:-1])
        return " ".join(parts[:-1])
    return name

def find_matching_game(scoreboard_games, home, away, sport_key=None):
    if not scoreboard_games: return None
    
    target_h = _clean_team_name(home)
    target_a = _clean_team_name(away)
    
    best = None
    best_score = 0.0
    
    for game in scoreboard_games:
        s_home = _clean_team_name(game.get("home_team", ""))
        s_away = _clean_team_name(game.get("away_team", ""))
        
        # Simple inclusion check first (handles UConn vs Connecticut)
        score = 0.0
        if target_h in s_home or s_home in target_h: score += 0.8
        if target_a in s_away or s_away in target_a: score += 0.8
        
        if score > best_score:
            best_score = score
            best = game
            
    # Lower threshold to 1.5 to be less picky
    if best and best_score >= 1.0:
        return best
    return None



if __name__ == "__main__":
    import asyncio

    async def _test() -> None:
        rows = await fetch_all_scoreboard_games()
        print("LIVE SCOREBOARD BRIDGE")
        print("total_games:", len(rows))

        by_sport: dict[str, int] = {}
        for r in rows:
            k = str(r.get("sport_key", "unknown"))
            by_sport[k] = by_sport.get(k, 0) + 1

        print("by_sport:", by_sport)

        if rows:
            print("first_game:", rows[0])

    asyncio.run(_test())
