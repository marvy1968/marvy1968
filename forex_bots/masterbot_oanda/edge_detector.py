#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

from live_sim_engine import simulate_game_vectorized


#############################################
# SAFE PARSERS & HELPERS
#############################################

def to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return default
            if v.endswith("%"):
                return float(v[:-1])
        return float(v)
    except Exception:
        return default


def clamp(x: float, low: float, high: float) -> float:
    return max(low, min(x, high))


def american_to_implied_prob(odds: Any, default: float = 0.50) -> float:
    o = to_float(odds, 0.0)
    if o == 0:
        return default
    try:
        if o > 0:
            return 100.0 / (o + 100.0)
        return abs(o) / (abs(o) + 100.0)
    except Exception:
        return default


def remaining_seconds(payload: Dict[str, Any]) -> int:
    minutes = int(to_float(payload.get("minutes_left", 0), 0))
    seconds = int(to_float(payload.get("seconds_left", 0), 0))
    return max(0, minutes * 60 + seconds)


#############################################
# PROJECTIONS
#############################################

def base_total_projection(payload: Dict[str, Any]) -> float:
    home_score = to_float(payload.get("home_score", 0))
    away_score = to_float(payload.get("away_score", 0))
    scored = home_score + away_score

    rem = remaining_seconds(payload)
    total_game_seconds = 40 * 60
    elapsed = max(1.0, total_game_seconds - rem)

    pace_proj = scored * (total_game_seconds / elapsed)
    market_total = to_float(payload.get("market_total", 150.0), 150.0)

    # Keep stronger market anchor early so slow starts do not create fake 99% unders
    progress = clamp(elapsed / total_game_seconds, 0.0, 1.0)
    market_weight = 0.55 if progress < 0.50 else 0.40
    pace_weight = 1.0 - market_weight

    return (pace_proj * pace_weight) + (market_total * market_weight)


def base_margin_projection(payload: Dict[str, Any]) -> float:
    home_score = to_float(payload.get("home_score", 0))
    away_score = to_float(payload.get("away_score", 0))
    current_margin = home_score - away_score

    home_spread = to_float(payload.get("home_spread_line", 0.0), 0.0)
    market_margin = -home_spread

    rem = remaining_seconds(payload)
    total_game_seconds = 40 * 60
    time_weight = clamp(rem / total_game_seconds, 0.0, 1.0)

    return (current_margin * (1.0 - time_weight * 0.70)) + (market_margin * (time_weight * 0.70))


#############################################
# SUSPICIOUS / CALIBRATION SUMMARY
#############################################

def detect_suspicious(
    *,
    raw_model_total: float,
    raw_model_margin: float,
    model_total: float,
    model_margin: float,
    market_total: float,
    home_spread_line: float,
) -> dict[str, Any]:
    total_gap = abs(model_total - market_total)
    margin_gap = abs(model_margin - (-home_spread_line))
    raw_total_shift = abs(raw_model_total - model_total)
    raw_margin_shift = abs(raw_model_margin - model_margin)

    suspicious_total = False
    suspicious_margin = False

    if total_gap >= 16.0:
        suspicious_total = True
    if raw_total_shift >= 18.0:
        suspicious_total = True

    if margin_gap >= 14.0:
        suspicious_margin = True
    if raw_margin_shift >= 10.0:
        suspicious_margin = True

    suspicious_any = suspicious_total or suspicious_margin

    return {
        "total_gap": round(total_gap, 2),
        "margin_gap": round(margin_gap, 2),
        "raw_total_shift": round(raw_total_shift, 2),
        "raw_margin_shift": round(raw_margin_shift, 2),
        "suspicious_total": suspicious_total,
        "suspicious_margin": suspicious_margin,
        "suspicious_any": suspicious_any,
    }


#############################################
# MAIN DETECTOR
#############################################

def detect_edges(payload: Dict[str, Any]) -> Dict[str, Any]:
    home_team = str(payload.get("home_team", ""))
    away_team = str(payload.get("away_team", ""))

    current_home_score = to_float(payload.get("home_score", 0))
    current_away_score = to_float(payload.get("away_score", 0))
    rem_seconds = remaining_seconds(payload)

    market_total = to_float(payload.get("market_total", 150.0), 150.0)
    home_spread_line = to_float(payload.get("home_spread_line", -3.5), -3.5)

    over_odds = payload.get("over_odds", -110)
    under_odds = payload.get("under_odds", -110)
    home_ml_odds = payload.get("home_ml_odds", -110)
    away_ml_odds = payload.get("away_ml_odds", -110)
    home_spread_odds = payload.get("home_spread_odds", -110)
    away_spread_odds = payload.get("away_spread_odds", -110)

    raw_model_total = base_total_projection(payload)
    raw_model_margin = base_margin_projection(payload)

    # Conservative smoothing before sim
    model_total = (raw_model_total * 0.75) + (market_total * 0.25)
    model_margin = (raw_model_margin * 0.75) + ((-home_spread_line) * 0.25)

    sim = simulate_game_vectorized(
        current_home_score=current_home_score,
        current_away_score=current_away_score,
        seconds_remaining=rem_seconds,
        model_total=model_total,
        model_margin=model_margin,
        home_spread_line=home_spread_line,
        market_total=market_total,
        n_sims=10000,
        seed=42,
    )

    imp_home_spread = american_to_implied_prob(home_spread_odds, 0.50)
    imp_away_spread = american_to_implied_prob(away_spread_odds, 0.50)
    imp_over = american_to_implied_prob(over_odds, 0.50)
    imp_under = american_to_implied_prob(under_odds, 0.50)
    imp_home_ml = american_to_implied_prob(home_ml_odds, 0.50)
    imp_away_ml = american_to_implied_prob(away_ml_odds, 0.50)

    home_cover_prob = float(sim["home_cover_prob"])
    away_cover_prob = float(sim["away_cover_prob"])
    over_prob = float(sim["over_prob"])
    under_prob = float(sim["under_prob"])
    home_ml_prob = float(sim["home_win_prob"])
    away_ml_prob = float(sim["away_win_prob"])

    home_spread_edge = max(0.0, home_cover_prob - imp_home_spread)
    away_spread_edge = max(0.0, away_cover_prob - imp_away_spread)
    over_edge = max(0.0, over_prob - imp_over)
    under_edge = max(0.0, under_prob - imp_under)
    home_ml_edge = max(0.0, home_ml_prob - imp_home_ml)
    away_ml_edge = max(0.0, away_ml_prob - imp_away_ml)

    calibration = detect_suspicious(
        raw_model_total=raw_model_total,
        raw_model_margin=raw_model_margin,
        model_total=model_total,
        model_margin=model_margin,
        market_total=market_total,
        home_spread_line=home_spread_line,
    )

    return {
        "home_team": home_team,
        "away_team": away_team,

        "raw_model_margin": round(raw_model_margin, 2),
        "model_margin": round(model_margin, 2),
        "raw_model_total": round(raw_model_total, 2),
        "model_total": round(model_total, 2),

        "home_cover_prob": round(home_cover_prob, 4),
        "away_cover_prob": round(away_cover_prob, 4),
        "over_prob": round(over_prob, 4),
        "under_prob": round(under_prob, 4),
        "home_ml_prob": round(home_ml_prob, 4),
        "away_ml_prob": round(away_ml_prob, 4),

        "home_spread_edge": round(home_spread_edge, 4),
        "away_spread_edge": round(away_spread_edge, 4),
        "over_edge": round(over_edge, 4),
        "under_edge": round(under_edge, 4),
        "home_ml_edge": round(home_ml_edge, 4),
        "away_ml_edge": round(away_ml_edge, 4),

        "push_spread_prob": round(float(sim.get("push_spread_prob", 0.0)), 4),
        "push_total_prob": round(float(sim.get("push_total_prob", 0.0)), 4),
        "ot_home_mean": round(float(sim.get("ot_home_mean", 0.0)), 4),
        "ot_away_mean": round(float(sim.get("ot_away_mean", 0.0)), 4),

        "calibration": calibration,
        "suspicious_any": bool(calibration["suspicious_any"]),
    }


if __name__ == "__main__":
    test_game = {
        "home_team": "Villanova Wildcats",
        "away_team": "Utah State Aggies",
        "home_score": 73,
        "away_score": 71,
        "minutes_left": 5,
        "seconds_left": 5,
        "market_total": 168.5,
        "home_spread_line": -0.5,
        "over_odds": -110,
        "under_odds": -110,
        "home_ml_odds": -120,
        "away_ml_odds": 100,
        "home_spread_odds": -110,
        "away_spread_odds": -110,
    }

    result = detect_edges(test_game)

    print("EDGE DETECTOR INTEGRATED TEST")
    for k, v in result.items():
        print(f"{k}: {v}")
