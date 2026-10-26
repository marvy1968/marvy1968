#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


#############################################
# HELPERS
#############################################

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = -110) -> int:
    try:
        return int(value)
    except Exception:
        return default


def implied_prob_from_american(odds: int) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def american_to_b(odds: int) -> float:
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def kelly_fraction(model_prob: float, odds: int, fraction: float = 0.25) -> float:
    p = clamp(model_prob, 0.001, 0.999)
    q = 1.0 - p
    b = american_to_b(odds)
    full_kelly = ((b * p) - q) / b
    return round(max(0.0, full_kelly) * fraction, 4)


#############################################
# CONFIG
#############################################

TEAM_TOTAL_ANCHOR_THRESHOLD = 4.0
TEAM_TOTAL_ANCHOR_WEIGHT = 0.45
EXTREME_TEAM_TOTAL_GAP = 8.0

PROB_SOFT_CAP_HIGH = 0.78
PROB_SOFT_CAP_LOW = 0.22

SUSPICION_KELLY_MULT = 0.50
SUSPICION_CONFIDENCE_PENALTY = 10.0
SUSPICION_EDGE_MULT = 0.82

MIN_EDGE_FOR_PROP = 0.03
MAX_KELLY_FOR_PROP = 0.07
MAX_DISPLAY_CONFIDENCE = 90.0

# adaptive weights
CLOSE_GAME_BONUS = 0.02
LATE_GAME_BONUS = 0.015
FOUL_CHAOS_OVER_BONUS = 0.02
BLOWOUT_UNDER_BONUS = 0.015
HIGH_VOLATILITY_EDGE_HAIRCUT = 0.92


#############################################
# DATA CLASS
#############################################

@dataclass
class TeamPropView:
    market: str
    team: str
    side: str
    line: float
    odds: int
    projected_final_raw: float
    projected_final_calibrated: float
    model_prob: float
    implied_prob: float
    edge: float
    confidence: float
    kelly_quarter: float
    suspicious: bool
    strength: str
    phase: str
    pace_bucket: str
    one_possession_game: bool
    blowout_flag: bool
    summary: str


#############################################
# CONTEXT
#############################################

def infer_game_context(payload: dict[str, Any]) -> dict[str, Any]:
    minutes_left = int(payload.get("minutes_left", 0))
    seconds_left = int(payload.get("seconds_left", 0))
    mode = str(payload.get("mode", "2h")).lower().strip()

    remaining_seconds = (minutes_left * 60) + seconds_left
    score_diff = abs(int(payload.get("home_score", 0)) - int(payload.get("away_score", 0)))

    if mode == "1h":
        phase = "1h"
    else:
        phase = "2h"

    if remaining_seconds >= 15 * 60:
        sigma = 7.0
    elif remaining_seconds >= 10 * 60:
        sigma = 6.0
    elif remaining_seconds >= 6 * 60:
        sigma = 4.8
    elif remaining_seconds >= 3 * 60:
        sigma = 3.9
    else:
        sigma = 3.0

    one_possession_game = score_diff <= 3
    blowout_flag = score_diff >= 12

    if phase == "2h" and remaining_seconds <= 120 and one_possession_game:
        pace_bucket = "foul_chaos"
    elif blowout_flag:
        pace_bucket = "slow_down"
    else:
        pace_bucket = "normal"

    return {
        "remaining_seconds": remaining_seconds,
        "phase": phase,
        "sigma": sigma,
        "one_possession_game": one_possession_game,
        "blowout_flag": blowout_flag,
        "pace_bucket": pace_bucket,
    }


def estimate_team_final_scores(payload: dict[str, Any]) -> tuple[float, float]:
    home_score = to_float(payload.get("home_score"), 0.0)
    away_score = to_float(payload.get("away_score"), 0.0)

    projected_home = payload.get("projected_home")
    projected_away = payload.get("projected_away")

    if projected_home is not None and projected_away is not None:
        return round(to_float(projected_home), 2), round(to_float(projected_away), 2)

    projected_total = payload.get("projected_total")
    if projected_total is not None:
        projected_total = to_float(projected_total, home_score + away_score)
        current_total = max(1.0, home_score + away_score)

        home_share = home_score / current_total
        home_share = (home_share * 0.85) + 0.075
        away_share = 1.0 - home_share

        return round(projected_total * home_share, 2), round(projected_total * away_share, 2)

    return round(home_score, 2), round(away_score, 2)


#############################################
# CALIBRATION
#############################################

def anchor_team_total_projection(projected_final: float, market_line: float) -> tuple[float, float, bool]:
    gap = market_line - projected_final
    abs_gap = abs(gap)

    if abs_gap <= TEAM_TOTAL_ANCHOR_THRESHOLD:
        return projected_final, abs_gap, False

    calibrated = projected_final + (gap * TEAM_TOTAL_ANCHOR_WEIGHT)
    return round(calibrated, 2), abs_gap, True


def smooth_probability(p: float) -> float:
    p = clamp(p, 0.001, 0.999)

    if p >= PROB_SOFT_CAP_HIGH:
        overflow = p - PROB_SOFT_CAP_HIGH
        p = PROB_SOFT_CAP_HIGH + (overflow * 0.18)

    if p <= PROB_SOFT_CAP_LOW:
        underflow = PROB_SOFT_CAP_LOW - p
        p = PROB_SOFT_CAP_LOW - (underflow * 0.18)

    return round(clamp(p, 0.03, 0.97), 4)


def detect_suspicion(team_gap: float) -> bool:
    return team_gap >= EXTREME_TEAM_TOTAL_GAP


#############################################
# PROBABILITY / ADAPTIVE LAYER
#############################################

def base_team_total_prob(projected_final: float, market_line: float, sigma: float) -> float:
    diff = projected_final - market_line
    raw = 0.50 + (diff / (2.0 * sigma))
    return round(clamp(raw, 0.03, 0.97), 4)


def apply_context_adjustments(
    side: str,
    prob: float,
    context: dict[str, Any],
) -> float:
    phase = context["phase"]
    remaining_seconds = context["remaining_seconds"]
    one_possession_game = context["one_possession_game"]
    blowout_flag = context["blowout_flag"]
    pace_bucket = context["pace_bucket"]

    adjusted = prob

    if one_possession_game and remaining_seconds <= 300:
        adjusted += CLOSE_GAME_BONUS if side == "over" else -CLOSE_GAME_BONUS

    if phase == "2h" and remaining_seconds <= 180:
        adjusted += LATE_GAME_BONUS if side == "over" else -LATE_GAME_BONUS

    if pace_bucket == "foul_chaos":
        adjusted += FOUL_CHAOS_OVER_BONUS if side == "over" else -FOUL_CHAOS_OVER_BONUS

    if blowout_flag:
        adjusted += -BLOWOUT_UNDER_BONUS if side == "over" else BLOWOUT_UNDER_BONUS

    return round(clamp(adjusted, 0.03, 0.97), 4)


def infer_confidence(edge: float, projected_final: float, market_line: float, suspicious: bool, context: dict[str, Any]) -> float:
    diff = abs(projected_final - market_line)
    conf = 50.0 + (edge * 210.0) + (min(diff, 8.0) * 1.2)

    if context["one_possession_game"]:
        conf += 2.0

    if context["pace_bucket"] == "foul_chaos":
        conf += 1.5

    if suspicious:
        conf -= SUSPICION_CONFIDENCE_PENALTY

    return round(clamp(conf, 50.0, MAX_DISPLAY_CONFIDENCE), 1)


def classify_strength(edge: float, confidence: float) -> str:
    if edge >= 0.12 and confidence >= 76:
        return "A"
    if edge >= 0.08 and confidence >= 68:
        return "B"
    if edge >= 0.05 and confidence >= 60:
        return "C"
    return "D"


def build_team_prop_summary(
    team: str,
    side: str,
    line: float,
    projected_final: float,
    edge: float,
    confidence: float,
    suspicious: bool,
    strength: str,
) -> str:
    return (
        f"{team} team total {side} {line}"
        f" | proj={projected_final:.2f}"
        f" | edge={edge:.4f}"
        f" | conf={confidence:.1f}%"
        f" | suspicious={suspicious}"
        f" | strength={strength}"
    )


#############################################
# BUILDERS
#############################################

def build_prop(
    team: str,
    side: str,
    line: float,
    odds: int,
    projected_final_raw: float,
    projected_final_calibrated: float,
    context: dict[str, Any],
) -> TeamPropView:
    sigma = context["sigma"]

    if side == "over":
        model_prob = base_team_total_prob(projected_final_calibrated, line, sigma)
    else:
        model_prob = round(1.0 - base_team_total_prob(projected_final_calibrated, line, sigma), 4)

    model_prob = apply_context_adjustments(side, model_prob, context)
    model_prob = smooth_probability(model_prob)

    implied_prob = implied_prob_from_american(odds)
    edge = round(model_prob - implied_prob, 4)

    team_gap = abs(projected_final_raw - line)
    suspicious = detect_suspicion(team_gap)

    if context["pace_bucket"] == "foul_chaos":
        edge = round(edge * HIGH_VOLATILITY_EDGE_HAIRCUT, 4)

    if suspicious:
        edge = round(edge * SUSPICION_EDGE_MULT, 4)

    confidence = infer_confidence(edge, projected_final_calibrated, line, suspicious, context)

    kelly = kelly_fraction(model_prob, odds)
    if suspicious:
        kelly = round(kelly * SUSPICION_KELLY_MULT, 4)
    kelly = round(min(kelly, MAX_KELLY_FOR_PROP), 4)

    strength = classify_strength(edge, confidence)

    return TeamPropView(
        market="team_total",
        team=team,
        side=side,
        line=line,
        odds=odds,
        projected_final_raw=projected_final_raw,
        projected_final_calibrated=projected_final_calibrated,
        model_prob=model_prob,
        implied_prob=implied_prob,
        edge=edge,
        confidence=confidence,
        kelly_quarter=kelly,
        suspicious=suspicious,
        strength=strength,
        phase=context["phase"],
        pace_bucket=context["pace_bucket"],
        one_possession_game=context["one_possession_game"],
        blowout_flag=context["blowout_flag"],
        summary=build_team_prop_summary(team, side, line, projected_final_calibrated, edge, confidence, suspicious, strength),
    )


#############################################
# MAIN ENGINE
#############################################

def evaluate_team_props_v3(payload: dict[str, Any]) -> dict[str, Any]:
    home_team = str(payload.get("home_team", "Home"))
    away_team = str(payload.get("away_team", "Away"))

    context = infer_game_context(payload)

    projected_home_raw, projected_away_raw = estimate_team_final_scores(payload)

    home_team_total_line = to_float(payload.get("home_team_total_line"), 0.0)
    away_team_total_line = to_float(payload.get("away_team_total_line"), 0.0)

    home_projected_cal, home_gap, home_anchored = anchor_team_total_projection(projected_home_raw, home_team_total_line)
    away_projected_cal, away_gap, away_anchored = anchor_team_total_projection(projected_away_raw, away_team_total_line)

    home_over_odds = to_int(payload.get("home_team_total_over_odds"), -110)
    home_under_odds = to_int(payload.get("home_team_total_under_odds"), -110)
    away_over_odds = to_int(payload.get("away_team_total_over_odds"), -110)
    away_under_odds = to_int(payload.get("away_team_total_under_odds"), -110)

    props: list[TeamPropView] = []

    props.append(build_prop(home_team, "over", home_team_total_line, home_over_odds, projected_home_raw, home_projected_cal, context))
    props.append(build_prop(home_team, "under", home_team_total_line, home_under_odds, projected_home_raw, home_projected_cal, context))
    props.append(build_prop(away_team, "over", away_team_total_line, away_over_odds, projected_away_raw, away_projected_cal, context))
    props.append(build_prop(away_team, "under", away_team_total_line, away_under_odds, projected_away_raw, away_projected_cal, context))

    ranked = sorted(props, key=lambda x: x.edge, reverse=True)

    best_props = []
    for prop in ranked:
        if prop.edge < MIN_EDGE_FOR_PROP:
            continue
        if prop.strength == "D":
            continue
        if prop.suspicious:
            continue
        best_props.append(asdict(prop))

    return {
        "home_team": home_team,
        "away_team": away_team,
        "projected_home_final_raw": round(projected_home_raw, 2),
        "projected_away_final_raw": round(projected_away_raw, 2),
        "projected_home_final_calibrated": round(home_projected_cal, 2),
        "projected_away_final_calibrated": round(away_projected_cal, 2),
        "home_team_total_line": home_team_total_line,
        "away_team_total_line": away_team_total_line,
        "home_gap": round(home_gap, 2),
        "away_gap": round(away_gap, 2),
        "home_anchored": home_anchored,
        "away_anchored": away_anchored,
        "context": context,
        "all_team_props": [asdict(p) for p in ranked],
        "best_team_props": best_props,
    }


#############################################
# DEMO
#############################################

if __name__ == "__main__":
    sample = {
        "home_team": "Duke",
        "away_team": "Auburn",
        "home_score": 50,
        "away_score": 45,
        "minutes_left": 4,
        "seconds_left": 0,
        "mode": "2h",
        "projected_home": 60.98,
        "projected_away": 55.57,
        "projected_total": 116.54,
        "home_team_total_line": 59.5,
        "away_team_total_line": 53.5,
        "home_team_total_over_odds": -110,
        "home_team_total_under_odds": -110,
        "away_team_total_over_odds": -110,
        "away_team_total_under_odds": -110,
    }

    result = evaluate_team_props_v3(sample)

    print("TEAM PROPS ENGINE V3")
    print("projected_home_final_raw:", result["projected_home_final_raw"])
    print("projected_away_final_raw:", result["projected_away_final_raw"])
    print("projected_home_final_calibrated:", result["projected_home_final_calibrated"])
    print("projected_away_final_calibrated:", result["projected_away_final_calibrated"])
    print("context:", result["context"])
    print("BEST TEAM PROPS:")
    for prop in result["best_team_props"]:
        print(
            prop["summary"],
            "| model_prob=", prop["model_prob"],
            "| implied_prob=", prop["implied_prob"],
            "| kelly_q=", prop["kelly_quarter"],
        )
