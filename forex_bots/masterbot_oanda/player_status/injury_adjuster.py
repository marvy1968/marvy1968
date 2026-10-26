#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


class InjuryAdjuster:
    """
    Converts injury snapshots into score/spread/total adjustments.

    Home spread convention:
    - negative = home favored
    - positive = home dog
    """

    def __init__(
        self,
        offensive_weight: float = 12.0,
        defensive_giveback_weight: float = 4.0,
        confidence_weight: float = 18.0,
    ) -> None:
        self.offensive_weight = offensive_weight
        self.defensive_giveback_weight = defensive_giveback_weight
        self.confidence_weight = confidence_weight

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    def _status_buckets(self, snapshot: dict[str, Any]) -> dict[str, float]:
        players = snapshot.get("players", []) or []

        out_impact = 0.0
        questionable_impact = 0.0
        doubtful_impact = 0.0
        probable_impact = 0.0

        out_count = 0
        questionable_count = 0
        doubtful_count = 0
        probable_count = 0

        for p in players:
            status = str(p.get("status", "")).strip().lower()
            impact = self._safe_float(p.get("impact_score", 0.0), 0.0)

            if "out" in status:
                out_count += 1
                out_impact += impact
            elif "doubt" in status:
                doubtful_count += 1
                doubtful_impact += impact
            elif "question" in status:
                questionable_count += 1
                questionable_impact += impact
            elif "probable" in status:
                probable_count += 1
                probable_impact += impact

        total_impact = self._safe_float(snapshot.get("team_impact_score", 0.0), 0.0)

        return {
            "team_impact_score": round(total_impact, 4),
            "out_impact": round(out_impact, 4),
            "questionable_impact": round(questionable_impact, 4),
            "doubtful_impact": round(doubtful_impact, 4),
            "probable_impact": round(probable_impact, 4),
            "out_count": float(out_count),
            "questionable_count": float(questionable_count),
            "doubtful_count": float(doubtful_count),
            "probable_count": float(probable_count),
        }

    def adjust_projection(
        self,
        away_snapshot: dict[str, Any],
        home_snapshot: dict[str, Any],
        feature_projection: dict[str, Any],
    ) -> dict[str, float]:
        away_base = self._status_buckets(away_snapshot)
        home_base = self._status_buckets(home_snapshot)

        away_expected = self._safe_float(feature_projection.get("away_expected_score", 0.0), 0.0)
        home_expected = self._safe_float(feature_projection.get("home_expected_score", 0.0), 0.0)

        away_off_penalty = (
            away_base["out_impact"] * self.offensive_weight
            + away_base["doubtful_impact"] * (self.offensive_weight * 0.65)
            + away_base["questionable_impact"] * (self.offensive_weight * 0.35)
        )
        home_off_penalty = (
            home_base["out_impact"] * self.offensive_weight
            + home_base["doubtful_impact"] * (self.offensive_weight * 0.65)
            + home_base["questionable_impact"] * (self.offensive_weight * 0.35)
        )

        away_def_giveback = (
            home_base["out_impact"] * self.defensive_giveback_weight
            + home_base["doubtful_impact"] * (self.defensive_giveback_weight * 0.60)
        )
        home_def_giveback = (
            away_base["out_impact"] * self.defensive_giveback_weight
            + away_base["doubtful_impact"] * (self.defensive_giveback_weight * 0.60)
        )

        adjusted_away_expected = max(0.0, away_expected - away_off_penalty + away_def_giveback)
        adjusted_home_expected = max(0.0, home_expected - home_off_penalty + home_def_giveback)

        adjusted_total = adjusted_away_expected + adjusted_home_expected

        # FIX: home spread convention
        # negative = home favored
        adjusted_home_spread = adjusted_away_expected - adjusted_home_expected

        uncertainty = (
            away_base["questionable_impact"]
            + home_base["questionable_impact"]
            + away_base["doubtful_impact"] * 0.6
            + home_base["doubtful_impact"] * 0.6
        )

        confidence_modifier = max(-25.0, min(25.0, -(uncertainty * self.confidence_weight)))

        return {
            "away_team_impact_score": round(away_base["team_impact_score"], 4),
            "home_team_impact_score": round(home_base["team_impact_score"], 4),
            "away_off_penalty": round(away_off_penalty, 2),
            "home_off_penalty": round(home_off_penalty, 2),
            "away_def_giveback": round(away_def_giveback, 2),
            "home_def_giveback": round(home_def_giveback, 2),
            "adjusted_away_expected": round(adjusted_away_expected, 2),
            "adjusted_home_expected": round(adjusted_home_expected, 2),
            "adjusted_total": round(adjusted_total, 2),
            "adjusted_home_spread": round(adjusted_home_spread, 2),
            "confidence_modifier": round(confidence_modifier, 2),
            "away_out_count": round(away_base["out_count"], 0),
            "home_out_count": round(home_base["out_count"], 0),
            "away_questionable_count": round(away_base["questionable_count"], 0),
            "home_questionable_count": round(home_base["questionable_count"], 0),
        }


if __name__ == "__main__":
    adjuster = InjuryAdjuster()

    away_snapshot = {
        "team": "Los Angeles Lakers",
        "team_impact_score": 0.18,
        "players": [
            {"player": "Star Guard", "status": "out", "impact_score": 0.12},
            {"player": "Wing", "status": "questionable", "impact_score": 0.06},
        ],
    }

    home_snapshot = {
        "team": "Indiana Pacers",
        "team_impact_score": 0.05,
        "players": [
            {"player": "Big", "status": "doubtful", "impact_score": 0.05},
        ],
    }

    feature_projection = {
        "away_expected_score": 111.5,
        "home_expected_score": 116.8,
    }

    out = adjuster.adjust_projection(
        away_snapshot=away_snapshot,
        home_snapshot=home_snapshot,
        feature_projection=feature_projection,
    )

    print("=== INJURY-ADJUSTED PROJECTION ===")
    for k, v in out.items():
        print(f"{k}: {v}")
