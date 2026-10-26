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
# DATA CLASS
#############################################

@dataclass
class TeamPropView:
    market: str
    team: str
    side: str
    line: float
    odds: int
    projected_final: float
    model_prob: float
    implied_prob: float
    edge: float
    confidence: float
    kelly_quarter: float
    summary: str


#############################################
# CORE TEAM PROP LOGIC
#############################################

def infer_game_context(payload: dict[str, Any]) -> dict[str, Any]:
    minutes_left = int(payload.get("minutes_left", 0))
    seconds_left = int(payload.get("seconds_left", 0))
    mode = str(payload.get("mode", "2h")).lower().strip()

    remaining_seconds = (minutes_left * 60) + seconds_left

    if mode == "1h":
        phase = "1h"
    else:
        phase = "2h"

    # rough volatility scale
    if remaining_seconds >= 15 * 60:
        sigma = 7.5
    elif remaining_seconds >= 10 * 60:
        sigma = 6.5
    elif remaining_seconds >= 6 * 60:
        sigma = 5.0
    elif remaining_seconds >= 3 * 60:
        sigma = 4.0
    else:
        sigma = 3.0

    return {
        "remaining_seconds": remaining_seconds,
        "phase": phase,
        "sigma": sigma,
    }


def estimate_team_final_scores(payload: dict[str, Any]) -> tuple[float, float]:
    """
    Priority:
    1. Use projected_home / projected_away if present
    2. Use projected_total + current score share
    3. Fallback to current score
    """
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
        away_share = away_score / current_total

        # slight shrink toward 50/50 so early-game splits do not explode
        home_share = (home_share * 0.85) + 0.075
        away_share = 1.0 - home_share

        return round(projected_total * home_share, 2), round(projected_total * away_share, 2)

    return round(home_score, 2), round(away_score, 2)


def team_total_prob(projected_final: float, market_line: float, sigma: float) -> float:
    """
    Linearized probability map around team total line.
    """
    diff = projected_final - market_line
    raw = 0.50 + (diff / (2.0 * sigma))
    return round(clamp(raw, 0.03, 0.97), 4)


def infer_confidence(edge: float, projected_final: float, market_line: float) -> float:
    diff = abs(projected_final - market_line)
    conf = 50.0 + (edge * 280.0) + (min(diff, 10.0) * 1.5)
    return round(clamp(conf, 50.0, 95.0), 1)


def build_team_prop_summary(
    team: str,
    side: str,
    line: float,
    projected_final: float,
    edge: float,
    confidence: float,
) -> str:
    return (
        f"{team} team total {side} {line}"
        f" | proj={projected_final:.2f}"
        f" | edge={edge:.4f}"
        f" | conf={confidence:.1f}%"
    )


#############################################
# MAIN ENGINE
#############################################

def evaluate_team_props(payload: dict[str, Any]) -> dict[str, Any]:
    home_team = str(payload.get("home_team", "Home"))
    away_team = str(payload.get("away_team", "Away"))

    context = infer_game_context(payload)
    sigma = context["sigma"]

    projected_home, projected_away = estimate_team_final_scores(payload)

    home_team_total_line = to_float(payload.get("home_team_total_line"), 0.0)
    away_team_total_line = to_float(payload.get("away_team_total_line"), 0.0)

    home_over_odds = to_int(payload.get("home_team_total_over_odds"), -110)
    home_under_odds = to_int(payload.get("home_team_total_under_odds"), -110)
    away_over_odds = to_int(payload.get("away_team_total_over_odds"), -110)
    away_under_odds = to_int(payload.get("away_team_total_under_odds"), -110)

    home_over_prob = team_total_prob(projected_home, home_team_total_line, sigma)
    home_under_prob = round(1.0 - home_over_prob, 4)

    away_over_prob = team_total_prob(projected_away, away_team_total_line, sigma)
    away_under_prob = round(1.0 - away_over_prob, 4)

    props: list[TeamPropView] = []

    # Home over
    home_over_implied = implied_prob_from_american(home_over_odds)
    home_over_edge = round(home_over_prob - home_over_implied, 4)
    home_over_conf = infer_confidence(home_over_edge, projected_home, home_team_total_line)
    props.append(
        TeamPropView(
            market="team_total",
            team=home_team,
            side="over",
            line=home_team_total_line,
            odds=home_over_odds,
            projected_final=projected_home,
            model_prob=home_over_prob,
            implied_prob=home_over_implied,
            edge=home_over_edge,
            confidence=home_over_conf,
            kelly_quarter=kelly_fraction(home_over_prob, home_over_odds),
            summary=build_team_prop_summary(home_team, "over", home_team_total_line, projected_home, home_over_edge, home_over_conf),
        )
    )

    # Home under
    home_under_implied = implied_prob_from_american(home_under_odds)
    home_under_edge = round(home_under_prob - home_under_implied, 4)
    home_under_conf = infer_confidence(home_under_edge, projected_home, home_team_total_line)
    props.append(
        TeamPropView(
            market="team_total",
            team=home_team,
            side="under",
            line=home_team_total_line,
            odds=home_under_odds,
            projected_final=projected_home,
            model_prob=home_under_prob,
            implied_prob=home_under_implied,
            edge=home_under_edge,
            confidence=home_under_conf,
            kelly_quarter=kelly_fraction(home_under_prob, home_under_odds),
            summary=build_team_prop_summary(home_team, "under", home_team_total_line, projected_home, home_under_edge, home_under_conf),
        )
    )

    # Away over
    away_over_implied = implied_prob_from_american(away_over_odds)
    away_over_edge = round(away_over_prob - away_over_implied, 4)
    away_over_conf = infer_confidence(away_over_edge, projected_away, away_team_total_line)
    props.append(
        TeamPropView(
            market="team_total",
            team=away_team,
            side="over",
            line=away_team_total_line,
            odds=away_over_odds,
            projected_final=projected_away,
            model_prob=away_over_prob,
            implied_prob=away_over_implied,
            edge=away_over_edge,
            confidence=away_over_conf,
            kelly_quarter=kelly_fraction(away_over_prob, away_over_odds),
            summary=build_team_prop_summary(away_team, "over", away_team_total_line, projected_away, away_over_edge, away_over_conf),
        )
    )

    # Away under
    away_under_implied = implied_prob_from_american(away_under_odds)
    away_under_edge = round(away_under_prob - away_under_implied, 4)
    away_under_conf = infer_confidence(away_under_edge, projected_away, away_team_total_line)
    props.append(
        TeamPropView(
            market="team_total",
            team=away_team,
            side="under",
            line=away_team_total_line,
            odds=away_under_odds,
            projected_final=projected_away,
            model_prob=away_under_prob,
            implied_prob=away_under_implied,
            edge=away_under_edge,
            confidence=away_under_conf,
            kelly_quarter=kelly_fraction(away_under_prob, away_under_odds),
            summary=build_team_prop_summary(away_team, "under", away_team_total_line, projected_away, away_under_edge, away_under_conf),
        )
    )

    ranked = sorted(props, key=lambda x: x.edge, reverse=True)

    best_props = []
    for prop in ranked:
        if prop.edge < 0.03:
            continue
        best_props.append(asdict(prop))

    return {
        "home_team": home_team,
        "away_team": away_team,
        "projected_home_final": round(projected_home, 2),
        "projected_away_final": round(projected_away, 2),
        "home_team_total_line": home_team_total_line,
        "away_team_total_line": away_team_total_line,
        "home_over_prob": home_over_prob,
        "home_under_prob": home_under_prob,
        "away_over_prob": away_over_prob,
        "away_under_prob": away_under_prob,
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

    result = evaluate_team_props(sample)

    print("TEAM PROPS ENGINE")
    print("projected_home_final:", result["projected_home_final"])
    print("projected_away_final:", result["projected_away_final"])
    print("BEST TEAM PROPS:")
    for prop in result["best_team_props"]:
        print(
            prop["summary"],
            "| model_prob=", prop["model_prob"],
            "| implied_prob=", prop["implied_prob"],
            "| kelly_q=", prop["kelly_quarter"],
        )
