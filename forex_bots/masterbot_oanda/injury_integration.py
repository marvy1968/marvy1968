#!/usr/bin/env python3
from __future__ import annotations

from player_status.injury_adjuster import InjuryAdjuster
from player_status.injury_scraper import fetch_team_injuries

adjuster = InjuryAdjuster()


def enrich_with_injuries(game_payload: dict) -> dict:
    try:
        away_team = game_payload.get("away_team")
        home_team = game_payload.get("home_team")
        projection = game_payload.get("feature_projection", {}) or {}

        if not away_team or not home_team:
            game_payload["injury_error"] = "missing away_team or home_team"
            return game_payload
        if not projection:
            game_payload["injury_error"] = "missing feature_projection"
            return game_payload

        away_snapshot = fetch_team_injuries(away_team, force_refresh=False).__dict__
        home_snapshot = fetch_team_injuries(home_team, force_refresh=False).__dict__

        adjusted = adjuster.adjust_projection(
            away_snapshot=away_snapshot,
            home_snapshot=home_snapshot,
            feature_projection=projection,
        )

        game_payload["injury_snapshot"] = {
            "away": away_snapshot,
            "home": home_snapshot,
        }
        game_payload["injury_projection"] = adjusted
        return game_payload

    except Exception as e:
        game_payload["injury_error"] = str(e)
        return game_payload
