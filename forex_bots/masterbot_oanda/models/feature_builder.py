#!/usr/bin/env python3
from __future__ import annotations

from math import sqrt
from typing import Any


class FeatureBuilder:
    LEAGUE_PROFILES = {
        "nba":   {"pace_mult": 1.03, "total_bias": 3.0,  "spread_mult": 1.00},
        "wnba":  {"pace_mult": 0.98, "total_bias": -1.5, "spread_mult": 0.97},
        "ncaab": {"pace_mult": 0.97, "total_bias": -2.5, "spread_mult": 1.04},
        "ncaaw": {"pace_mult": 1.01, "total_bias": 1.5,  "spread_mult": 1.01},
        "euro":  {"pace_mult": 0.94, "total_bias": -5.0, "spread_mult": 0.96},
    }

    def __init__(self, rolling_window: int = 5) -> None:
        self.window = rolling_window

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _std(values: list[float]) -> float:
        if not values:
            return 0.0
        mu = sum(values) / len(values)
        var = sum((x - mu) ** 2 for x in values) / len(values)
        return sqrt(var)

    def _league_profile(self, league: str) -> dict[str, float]:
        return self.LEAGUE_PROFILES.get(
            str(league or "").lower(),
            {"pace_mult": 1.0, "total_bias": 0.0, "spread_mult": 1.0},
        )

    def build_team_features(self, team_data: dict[str, Any]) -> dict[str, float]:
        games = team_data.get("recent_games", []) or []
        league = str(team_data.get("league", "")).lower()
        recent = games[-self.window:] if len(games) > self.window else games[:]

        if not recent:
            return {
                "avg_scored": 0.0,
                "avg_allowed": 0.0,
                "avg_margin": 0.0,
                "win_rate": 0.0,
                "over_rate": 0.0,
                "avg_total": 0.0,
                "std_scored": 0.0,
                "std_allowed": 0.0,
                "home_avg_scored": 0.0,
                "home_avg_allowed": 0.0,
                "away_avg_scored": 0.0,
                "away_avg_allowed": 0.0,
                "tempo_proxy": 0.0,
                "games_sampled": 0.0,
                "league": league,
            }

        scored_list: list[float] = []
        allowed_list: list[float] = []
        margin_list: list[float] = []
        total_list: list[float] = []
        home_scored: list[float] = []
        home_allowed: list[float] = []
        away_scored: list[float] = []
        away_allowed: list[float] = []
        win_count = 0
        over_count = 0

        for game in recent:
            scored = self._safe_float(game.get("team_points", 0), 0.0)
            allowed = self._safe_float(game.get("opp_points", 0), 0.0)
            home_away = str(game.get("home_away", "")).strip().lower()

            scored_list.append(scored)
            allowed_list.append(allowed)
            margin_list.append(scored - allowed)
            total_list.append(scored + allowed)

            if scored > allowed:
                win_count += 1

            if home_away == "home":
                home_scored.append(scored)
                home_allowed.append(allowed)
            elif home_away == "away":
                away_scored.append(scored)
                away_allowed.append(allowed)

        avg_total = self._mean(total_list)
        for total in total_list:
            if total > avg_total:
                over_count += 1

        tempo_proxy = avg_total

        return {
            "avg_scored": round(self._mean(scored_list), 2),
            "avg_allowed": round(self._mean(allowed_list), 2),
            "avg_margin": round(self._mean(margin_list), 2),
            "win_rate": round(win_count / len(recent), 3),
            "over_rate": round(over_count / len(recent), 3),
            "avg_total": round(avg_total, 2),
            "std_scored": round(self._std(scored_list), 2),
            "std_allowed": round(self._std(allowed_list), 2),
            "home_avg_scored": round(self._mean(home_scored), 2),
            "home_avg_allowed": round(self._mean(home_allowed), 2),
            "away_avg_scored": round(self._mean(away_scored), 2),
            "away_avg_allowed": round(self._mean(away_allowed), 2),
            "tempo_proxy": round(tempo_proxy, 2),
            "games_sampled": float(len(recent)),
            "league": league,
        }

    def build_matchup_features(
        self,
        away_features: dict[str, float],
        home_features: dict[str, float],
        market_data: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        market = market_data or {}
        league = str(
            market.get("league")
            or away_features.get("league")
            or home_features.get("league")
            or ""
        ).lower()
        profile = self._league_profile(league)

        away_off = self._safe_float(away_features.get("avg_scored", 0.0))
        away_def = self._safe_float(away_features.get("avg_allowed", 0.0))
        home_off = self._safe_float(home_features.get("avg_scored", 0.0))
        home_def = self._safe_float(home_features.get("avg_allowed", 0.0))

        away_off_split = self._safe_float(away_features.get("away_avg_scored", 0.0)) or away_off
        away_def_split = self._safe_float(away_features.get("away_avg_allowed", 0.0)) or away_def
        home_off_split = self._safe_float(home_features.get("home_avg_scored", 0.0)) or home_off
        home_def_split = self._safe_float(home_features.get("home_avg_allowed", 0.0)) or home_def

        away_expected = (away_off_split + home_def_split) / 2.0
        home_expected = (home_off_split + away_def_split) / 2.0

        away_tempo = self._safe_float(away_features.get("tempo_proxy", away_off + away_def))
        home_tempo = self._safe_float(home_features.get("tempo_proxy", home_off + home_def))
        tempo_blend = ((away_tempo + home_tempo) / 2.0) / 2.0

        raw_total = away_expected + home_expected
        total_gap_vs_tempo = tempo_blend - raw_total
        pace_adjustment = total_gap_vs_tempo * 0.18
        system_proj_total = (raw_total + pace_adjustment) * profile["pace_mult"] + profile["total_bias"]

        raw_home_spread = away_expected - home_expected
        system_proj_home_spread = raw_home_spread * profile["spread_mult"]

        away_minus_home_margin = self._safe_float(away_features.get("avg_margin", 0.0)) - self._safe_float(
            home_features.get("avg_margin", 0.0)
        )

        market_total = self._safe_float(market.get("total", market.get("market_total", 0.0)), 0.0)
        market_spread = self._safe_float(
            market.get("spread", market.get("market_spread", market.get("home_spread_line", 0.0))),
            0.0,
        )

        return {
            "league": league,
            "away_expected_score": round(away_expected, 2),
            "home_expected_score": round(home_expected, 2),
            "tempo_blend": round(tempo_blend, 2),
            "pace_adjustment": round(pace_adjustment, 2),
            "system_proj_total": round(system_proj_total, 2),
            "away_minus_home_margin": round(away_minus_home_margin, 2),
            "system_proj_home_spread": round(system_proj_home_spread, 2),
            "market_total": round(market_total, 2),
            "market_spread": round(market_spread, 2),
            "total_edge_delta": round(system_proj_total - market_total, 2),
            "spread_edge_delta": round(system_proj_home_spread - market_spread, 2),
            "away_win_rate": round(self._safe_float(away_features.get("win_rate", 0.0)), 3),
            "home_win_rate": round(self._safe_float(home_features.get("win_rate", 0.0)), 3),
            "away_over_rate": round(self._safe_float(away_features.get("over_rate", 0.0)), 3),
            "home_over_rate": round(self._safe_float(home_features.get("over_rate", 0.0)), 3),
        }


if __name__ == "__main__":
    fb = FeatureBuilder(rolling_window=5)
    print("FeatureBuilder OK:", isinstance(fb, FeatureBuilder))
