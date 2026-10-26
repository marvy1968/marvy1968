#!/usr/bin/env python3
from __future__ import annotations

import os
import re
from typing import Any

import aiohttp
from dotenv import load_dotenv

from data_engine.team_stat_lookup import get_team_stat_profile

load_dotenv(".env")

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

# The Odds API sport keys
SPORT_KEY_MAP = {
    "mens": "basketball_ncaab",
    "women": "basketball_wncaab",
}

# Book list used when available
DEFAULT_BOOKS = "draftkings,fanduel,betmgm,caesars,espnbet,betrivers"

# Safe defaults if markets are missing
DEFAULT_MARKET_TOTAL = 147.5
DEFAULT_HOME_SPREAD = -3.5
DEFAULT_AWAY_SPREAD = 3.5
DEFAULT_HOME_ML = -145
DEFAULT_AWAY_ML = 125
DEFAULT_OVER_ODDS = -110
DEFAULT_UNDER_ODDS = -110
DEFAULT_SPREAD_ODDS = -110

ODDS_BASE_URL = "https://api.the-odds-api.com/v4/sports"


def normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


async def _fetch_json(url: str, params: dict[str, Any]) -> Any:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()


async def fetch_odds_events_async(mode: str = "mens") -> list[dict[str, Any]]:
    """
    Pulls current NCAAB event + bookmaker data from The Odds API.
    Returns a list of event dicts.
    """
    if not ODDS_API_KEY:
        return []

    sport_key = SPORT_KEY_MAP.get(mode, SPORT_KEY_MAP["mens"])
    url = f"{ODDS_BASE_URL}/{sport_key}/odds"

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "bookmakers": DEFAULT_BOOKS,
        "oddsFormat": "american",
    }

    try:
        data = await _fetch_json(url, params)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _extract_market_snapshot(event: dict[str, Any]) -> dict[str, Any]:
    """
    Reads the first valid bookmaker/market set it can find.
    Returns a flattened market snapshot.
    """
    home_team = str(event.get("home_team", "Home"))
    away_team = str(event.get("away_team", "Away"))

    snapshot = {
        "market_total": DEFAULT_MARKET_TOTAL,
        "over_odds": DEFAULT_OVER_ODDS,
        "under_odds": DEFAULT_UNDER_ODDS,
        "home_ml_odds": DEFAULT_HOME_ML,
        "away_ml_odds": DEFAULT_AWAY_ML,
        "home_spread_line": DEFAULT_HOME_SPREAD,
        "away_spread_line": DEFAULT_AWAY_SPREAD,
        "home_spread_odds": DEFAULT_SPREAD_ODDS,
        "away_spread_odds": DEFAULT_SPREAD_ODDS,
    }

    bookmakers = event.get("bookmakers", [])
    if not isinstance(bookmakers, list):
        return snapshot

    for book in bookmakers:
        markets = book.get("markets", [])
        if not isinstance(markets, list):
            continue

        for market in markets:
            key = market.get("key")
            outcomes = market.get("outcomes", [])
            if not isinstance(outcomes, list):
                continue

            if key == "h2h":
                for out in outcomes:
                    name = str(out.get("name", ""))
                    price = int(out.get("price", DEFAULT_HOME_ML))
                    if name == home_team:
                        snapshot["home_ml_odds"] = price
                    elif name == away_team:
                        snapshot["away_ml_odds"] = price

            elif key == "spreads":
                for out in outcomes:
                    name = str(out.get("name", ""))
                    price = int(out.get("price", DEFAULT_SPREAD_ODDS))
                    point = float(out.get("point", 0.0))
                    if name == home_team:
                        snapshot["home_spread_line"] = point
                        snapshot["home_spread_odds"] = price
                    elif name == away_team:
                        snapshot["away_spread_line"] = point
                        snapshot["away_spread_odds"] = price

            elif key == "totals":
                for out in outcomes:
                    name = str(out.get("name", "")).lower()
                    price = int(out.get("price", DEFAULT_OVER_ODDS))
                    point = float(out.get("point", DEFAULT_MARKET_TOTAL))
                    snapshot["market_total"] = point
                    if name == "over":
                        snapshot["over_odds"] = price
                    elif name == "under":
                        snapshot["under_odds"] = price

        # if we found anything, stop at first good bookmaker
        return snapshot

    return snapshot


def _find_event_by_matchup(
    away_team: str,
    home_team: str,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    away_norm = normalize_name(away_team)
    home_norm = normalize_name(home_team)

    for event in events:
        ev_home = normalize_name(str(event.get("home_team", "")))
        ev_away = normalize_name(str(event.get("away_team", "")))

        if ev_home == home_norm and ev_away == away_norm:
            return event

        # allow reversed text if needed
        if ev_home == away_norm and ev_away == home_norm:
            return event

    return None


def _extract_scores_from_event(event: dict[str, Any]) -> tuple[int, int]:
    """
    Best-effort score extraction.
    The Odds API may not always provide live scores depending on endpoint.
    """
    home_score = 0
    away_score = 0

    scores = event.get("scores")
    if isinstance(scores, list):
        for item in scores:
            name = str(item.get("name", ""))
            score = item.get("score")
            try:
                score_val = int(score)
            except Exception:
                continue

            if name == event.get("home_team"):
                home_score = score_val
            elif name == event.get("away_team"):
                away_score = score_val

    return home_score, away_score


def _mode_to_default_clock(mode: str) -> tuple[int, int]:
    mode = mode.lower().strip()
    if mode == "1h":
        return 10, 0
    if mode == "2h":
        return 12, 0
    return 8, 0


def enrich_payload_with_team_stats(payload: dict[str, Any]) -> dict[str, Any]:
    home_team = str(payload.get("home_team", "")).strip()
    away_team = str(payload.get("away_team", "")).strip()

    home_stats = get_team_stat_profile(home_team)
    away_stats = get_team_stat_profile(away_team)

    payload.update({
        "home_off_3pa_rate": home_stats["off_3pa_rate"],
        "home_off_3p_pct": home_stats["off_3p_pct"],
        "home_off_2p_pct": home_stats["off_2p_pct"],
        "home_off_ft_rate": home_stats["off_ft_rate"],
        "home_off_ft_pct": home_stats["off_ft_pct"],
        "home_off_tov_rate": home_stats["off_tov_rate"],
        "home_off_orb_rate": home_stats["off_orb_rate"],

        "home_def_3pa_rate_allowed": home_stats["def_3pa_rate_allowed"],
        "home_def_3p_pct_allowed": home_stats["def_3p_pct_allowed"],
        "home_def_2p_pct_allowed": home_stats["def_2p_pct_allowed"],
        "home_def_ft_rate_allowed": home_stats["def_ft_rate_allowed"],
        "home_def_tov_forced_rate": home_stats["def_tov_forced_rate"],
        "home_def_drb_rate": home_stats["def_drb_rate"],

        "away_off_3pa_rate": away_stats["off_3pa_rate"],
        "away_off_3p_pct": away_stats["off_3p_pct"],
        "away_off_2p_pct": away_stats["off_2p_pct"],
        "away_off_ft_rate": away_stats["off_ft_rate"],
        "away_off_ft_pct": away_stats["off_ft_pct"],
        "away_off_tov_rate": away_stats["off_tov_rate"],
        "away_off_orb_rate": away_stats["off_orb_rate"],

        "away_def_3pa_rate_allowed": away_stats["def_3pa_rate_allowed"],
        "away_def_3p_pct_allowed": away_stats["def_3p_pct_allowed"],
        "away_def_2p_pct_allowed": away_stats["def_2p_pct_allowed"],
        "away_def_ft_rate_allowed": away_stats["def_ft_rate_allowed"],
        "away_def_tov_forced_rate": away_stats["def_tov_forced_rate"],
        "away_def_drb_rate": away_stats["def_drb_rate"],
    })

    return payload


async def build_live_payload_from_matchup(
    away_team: str,
    home_team: str,
    mode: str = "full",
) -> dict[str, Any]:
    """
    Build a live payload for manual_live_eval.py.

    This function:
    1. pulls odds events
    2. finds the matching event
    3. extracts market data + any live scores if available
    4. enriches the payload with Phase 4 team stats
    """
    events = await fetch_odds_events_async("mens")
    matched_event = _find_event_by_matchup(away_team, home_team, events)

    minutes_left, seconds_left = _mode_to_default_clock(mode)
    home_score = 0
    away_score = 0

    payload: dict[str, Any] = {
        "away_team": away_team,
        "home_team": home_team,
        "mode": mode.lower().strip(),
        "home_score": home_score,
        "away_score": away_score,
        "minutes_left": minutes_left,
        "seconds_left": seconds_left,
        "market_total": DEFAULT_MARKET_TOTAL,
        "over_odds": DEFAULT_OVER_ODDS,
        "under_odds": DEFAULT_UNDER_ODDS,
        "home_ml_odds": DEFAULT_HOME_ML,
        "away_ml_odds": DEFAULT_AWAY_ML,
        "home_spread_line": DEFAULT_HOME_SPREAD,
        "away_spread_line": DEFAULT_AWAY_SPREAD,
        "home_spread_odds": DEFAULT_SPREAD_ODDS,
        "away_spread_odds": DEFAULT_SPREAD_ODDS,
        "baseline_points_per_possession": 1.02,
        "recent_points_per_possession": 0.96,
        "recent_possessions_per_min": 3.1,
        "baseline_possessions_per_min": 3.0,
    }

    if matched_event:
        payload["home_team"] = str(matched_event.get("home_team", home_team))
        payload["away_team"] = str(matched_event.get("away_team", away_team))

        home_score, away_score = _extract_scores_from_event(matched_event)
        payload["home_score"] = home_score
        payload["away_score"] = away_score

        market_snapshot = _extract_market_snapshot(matched_event)
        payload.update(market_snapshot)

    payload = enrich_payload_with_team_stats(payload)
    return payload
