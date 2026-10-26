#!/usr/bin/env python3
from __future__ import annotations

from models.feature_builder import FeatureBuilder
from stats_scraper.team_stats_scraper import fetch_team_recent_data

builder = FeatureBuilder(rolling_window=5)


def _infer_league(game_payload: dict) -> str:
    sport_key = str(game_payload.get("sport_key", "")).lower()
    league = str(game_payload.get("league", "")).lower()

    if league:
        return league
    if "nba" in sport_key:
        return "nba"
    if "wnba" in sport_key:
        return "wnba"
    if "wncaab" in sport_key:
        return "ncaaw"
    if "ncaab" in sport_key:
        return "ncaab"
    if "euro" in sport_key:
        return "euro"

    return "ncaab"


def enrich_with_team_features(game_payload: dict) -> dict:
    try:
        away_team = game_payload.get("away_team")
        home_team = game_payload.get("home_team")
        league = _infer_league(game_payload)

        if not away_team or not home_team:
            game_payload["feature_error"] = "missing away_team or home_team"
            return game_payload

        away_data = fetch_team_recent_data(
            team_name=away_team,
            league=league,
            force_refresh=False,
            recent_n=5,
        )
        home_data = fetch_team_recent_data(
            team_name=home_team,
            league=league,
            force_refresh=False,
            recent_n=5,
        )

        away_features = builder.build_team_features(away_data.__dict__)
        home_features = builder.build_team_features(home_data.__dict__)

        matchup = builder.build_matchup_features(
            away_features=away_features,
            home_features=home_features,
            market_data={
                "total": game_payload.get("market_total", 0.0),
                "spread": game_payload.get("home_spread_line", game_payload.get("market_spread", 0.0)),
            },
        )

        game_payload["league"] = league
        game_payload["team_features"] = {
            "away": away_features,
            "home": home_features,
        }
        game_payload["feature_projection"] = matchup
        return game_payload

    except Exception as e:
        game_payload["feature_error"] = str(e)
        return game_payload
