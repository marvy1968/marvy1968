#!/usr/bin/env python3
from __future__ import annotations

import json
import random
from typing import Any


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def american_to_decimal(odds: int) -> float:
    if odds > 0:
        return 1.0 + (odds / 100.0)
    return 1.0 + (100.0 / abs(odds))


def implied_prob_from_american(odds: int) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def calculate_edge(model_prob: float, implied_prob: float) -> float:
    return model_prob - implied_prob


def calculate_ev(model_prob: float, odds: int) -> float:
    decimal_odds = american_to_decimal(odds)
    return (model_prob * (decimal_odds - 1.0)) - (1.0 - model_prob)


def estimate_auto_pace(
    mode: str,
    home_score: int,
    away_score: int,
    minutes_left: int,
    seconds_left: int,
    baseline_ppm: float,
) -> tuple[float, float]:
    total_scored = home_score + away_score
    remaining_minutes = minutes_left + (seconds_left / 60.0)

    if mode in {"1h", "2h"}:
        segment_length = 20.0
    else:
        segment_length = 40.0

    elapsed = max(0.5, segment_length - remaining_minutes)
    pts_per_min = total_scored / elapsed

    recent_ppm = pts_per_min / 1.0
    recent_ppm = _clamp(recent_ppm, 1.8, 4.2)
    baseline_ppm = _clamp(baseline_ppm, 1.8, 4.2)

    return recent_ppm, baseline_ppm


def apply_score_differential_pace_model(
    mode: str,
    score_diff: int,
    recent_ppm: float,
    baseline_ppm: float,
    minutes_left: int,
) -> tuple[float, str]:
    blended = (0.60 * recent_ppm) + (0.40 * baseline_ppm)
    note = "neutral pace blend"

    if mode == "2h":
        if abs(score_diff) <= 6 and minutes_left <= 12:
            blended *= 1.06
            note = "close 2H pace-up applied"
        elif abs(score_diff) >= 12 and minutes_left <= 12:
            blended *= 0.94
            note = "wide-lead 2H pace-down applied"

    elif mode == "full":
        if abs(score_diff) <= 5 and minutes_left <= 8:
            blended *= 1.04
            note = "close-game pace-up applied"
        elif abs(score_diff) >= 15 and minutes_left <= 8:
            blended *= 0.93
            note = "blowout pace-down applied"

    elif mode == "1h":
        if abs(score_diff) <= 5 and minutes_left <= 8:
            blended *= 1.03
            note = "close 1H pace-up applied"

    return _clamp(blended, 1.8, 4.4), note


def build_top_bets(
    home_team: str,
    away_team: str,
    market_total: float,
    home_spread_line: float,
    away_spread_line: float,
    over_prob: float,
    under_prob: float,
    home_win_prob: float,
    away_win_prob: float,
    home_cover_prob: float,
    away_cover_prob: float,
    over_odds: int,
    under_odds: int,
    home_ml_odds: int,
    away_ml_odds: int,
    home_spread_odds: int,
    away_spread_odds: int,
    min_edge: float = 0.03,
) -> list[dict[str, Any]]:
    bets: list[dict[str, Any]] = []

    market_candidates = [
        {
            "label": f"OVER {market_total}",
            "market": "total",
            "side": "over",
            "line": market_total,
            "model_prob": over_prob,
            "odds": over_odds,
        },
        {
            "label": f"UNDER {market_total}",
            "market": "total",
            "side": "under",
            "line": market_total,
            "model_prob": under_prob,
            "odds": under_odds,
        },
        {
            "label": f"{home_team} ML",
            "market": "moneyline",
            "side": "home",
            "team": home_team,
            "model_prob": home_win_prob,
            "odds": home_ml_odds,
        },
        {
            "label": f"{away_team} ML",
            "market": "moneyline",
            "side": "away",
            "team": away_team,
            "model_prob": away_win_prob,
            "odds": away_ml_odds,
        },
        {
            "label": f"{home_team} {home_spread_line}",
            "market": "spread",
            "side": "home",
            "team": home_team,
            "line": home_spread_line,
            "model_prob": home_cover_prob,
            "odds": home_spread_odds,
        },
        {
            "label": f"{away_team} {away_spread_line}",
            "market": "spread",
            "side": "away",
            "team": away_team,
            "line": away_spread_line,
            "model_prob": away_cover_prob,
            "odds": away_spread_odds,
        },
    ]

    for c in market_candidates:
        implied_prob = implied_prob_from_american(c["odds"])
        edge = calculate_edge(c["model_prob"], implied_prob)
        ev = calculate_ev(c["model_prob"], c["odds"])

        if edge < min_edge:
            continue

        stake = min(0.02, max(0.0, ev * 0.35))

        bets.append(
            {
                "label": c["label"],
                "market": c["market"],
                "side": c["side"],
                "line": c.get("line"),
                "team": c.get("team"),
                "odds": c["odds"],
                "model_prob": round(c["model_prob"], 4),
                "implied_prob": round(implied_prob, 4),
                "edge_display": round(edge, 4),
                "ev": round(ev, 4),
                "stake": round(stake, 4),
            }
        )

    bets.sort(key=lambda x: x["edge_display"], reverse=True)
    return bets[:3]


def evaluate_live_game(payload: dict[str, Any]) -> dict[str, Any]:
    home_team = str(payload["home_team"])
    away_team = str(payload["away_team"])
    home_score = int(payload["home_score"])
    away_score = int(payload["away_score"])
    minutes_left = int(payload["minutes_left"])
    seconds_left = int(payload["seconds_left"])
    mode = str(payload["mode"]).lower()

    market_total = float(payload["market_total"])
    over_odds = int(payload.get("over_odds", -110))
    under_odds = int(payload.get("under_odds", -110))
    home_ml_odds = int(payload.get("home_ml_odds", -110))
    away_ml_odds = int(payload.get("away_ml_odds", -110))
    home_spread_line = float(payload.get("home_spread_line", 0.0))
    away_spread_line = float(payload.get("away_spread_line", 0.0))
    home_spread_odds = int(payload.get("home_spread_odds", -110))
    away_spread_odds = int(payload.get("away_spread_odds", -110))

    baseline_ppp = float(payload.get("baseline_points_per_possession", 1.02))
    recent_ppp = float(payload.get("recent_points_per_possession", 0.88))
    recent_ppm = float(payload.get("recent_possessions_per_min", 3.1))
    baseline_ppm = float(payload.get("baseline_possessions_per_min", 3.0))

    score_diff = home_score - away_score

    if recent_ppm > 5.0 or baseline_ppm > 5.0 or recent_ppm < 1.0 or baseline_ppm < 1.0:
        recent_ppm, baseline_ppm = estimate_auto_pace(
            mode=mode,
            home_score=home_score,
            away_score=away_score,
            minutes_left=minutes_left,
            seconds_left=seconds_left,
            baseline_ppm=3.0,
        )

    blended_ppm, pace_note = apply_score_differential_pace_model(
        mode=mode,
        score_diff=score_diff,
        recent_ppm=recent_ppm,
        baseline_ppm=baseline_ppm,
        minutes_left=minutes_left,
    )

    blended_ppp = _clamp((0.55 * recent_ppp) + (0.45 * baseline_ppp), 0.74, 1.24)
    remaining_minutes = minutes_left + (seconds_left / 60.0)
    expected_remaining_possessions = max(1.0, blended_ppm * remaining_minutes)

    num_sims = 10000

    sum_home_final = 0.0
    sum_away_final = 0.0
    sum_total_final = 0.0

    home_wins = 0
    home_covers = 0
    overs = 0

    for _ in range(num_sims):
        sim_home = float(home_score)
        sim_away = float(away_score)

        # more realistic variance
        poss = max(1, int(random.gauss(expected_remaining_possessions, 4.5)))

        # allow game-flow drift per simulation
        sim_home_share = 0.5 + _clamp(random.gauss(score_diff / 40.0, 0.06), -0.18, 0.18)

        for _p in range(poss):
            pts = max(0.0, random.gauss(blended_ppp, 0.48))

            if random.random() < sim_home_share:
                sim_home += pts
            else:
                sim_away += pts

        sim_total = sim_home + sim_away

        sum_home_final += sim_home
        sum_away_final += sim_away
        sum_total_final += sim_total

        if sim_home > sim_away:
            home_wins += 1

        if (sim_home - sim_away) + home_spread_line > 0:
            home_covers += 1

        if sim_total > market_total:
            overs += 1

    projected_home = sum_home_final / num_sims
    projected_away = sum_away_final / num_sims
    projected_total = sum_total_final / num_sims

    home_win_prob = home_wins / num_sims
    away_win_prob = 1.0 - home_win_prob
    home_cover_prob = home_covers / num_sims
    away_cover_prob = 1.0 - home_cover_prob
    over_prob = overs / num_sims
    under_prob = 1.0 - over_prob

    over_implied = implied_prob_from_american(over_odds)
    under_implied = implied_prob_from_american(under_odds)

    over_edge = calculate_edge(over_prob, over_implied)
    under_edge = calculate_edge(under_prob, under_implied)

    inefficiency_is_inefficient = False
    inefficiency_gap = 0.0
    inefficiency_direction = "NONE"

    if over_edge >= 0.03 and over_edge >= under_edge:
        inefficiency_is_inefficient = True
        inefficiency_gap = over_edge
        inefficiency_direction = "OVER"
    elif under_edge >= 0.03 and under_edge > over_edge:
        inefficiency_is_inefficient = True
        inefficiency_gap = under_edge
        inefficiency_direction = "UNDER"

    top_bets = build_top_bets(
        home_team=home_team,
        away_team=away_team,
        market_total=market_total,
        home_spread_line=home_spread_line,
        away_spread_line=away_spread_line,
        over_prob=over_prob,
        under_prob=under_prob,
        home_win_prob=home_win_prob,
        away_win_prob=away_win_prob,
        home_cover_prob=home_cover_prob,
        away_cover_prob=away_cover_prob,
        over_odds=over_odds,
        under_odds=under_odds,
        home_ml_odds=home_ml_odds,
        away_ml_odds=away_ml_odds,
        home_spread_odds=home_spread_odds,
        away_spread_odds=away_spread_odds,
        min_edge=0.03,
    )

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "minutes_left": minutes_left,
        "seconds_left": seconds_left,
        "mode": mode,
        "market_total": market_total,
        "projected_home": round(projected_home, 2),
        "projected_away": round(projected_away, 2),
        "projected_total": round(projected_total, 2),
        "home_win_prob": round(home_win_prob, 4),
        "away_win_prob": round(away_win_prob, 4),
        "home_cover_prob": round(home_cover_prob, 4),
        "away_cover_prob": round(away_cover_prob, 4),
        "over_prob": round(over_prob, 4),
        "under_prob": round(under_prob, 4),
        "inefficiency_is_inefficient": inefficiency_is_inefficient,
        "inefficiency_gap": round(inefficiency_gap, 4),
        "inefficiency_direction": inefficiency_direction,
        "blended_ppm": round(blended_ppm, 3),
        "blended_ppp": round(blended_ppp, 3),
        "pace_note": pace_note,
        "top_bets": top_bets,
    }


if __name__ == "__main__":
    sample_payload = {
        "home_team": "Duke",
        "away_team": "Auburn",
        "home_score": 50,
        "away_score": 45,
        "minutes_left": 12,
        "seconds_left": 0,
        "mode": "2h",
        "market_total": 147.5,
        "over_odds": -110,
        "under_odds": -110,
        "home_ml_odds": -145,
        "away_ml_odds": 125,
        "home_spread_line": -3.5,
        "away_spread_line": 3.5,
        "home_spread_odds": -110,
        "away_spread_odds": -110,
        "baseline_points_per_possession": 1.02,
        "recent_points_per_possession": 0.96,
        "recent_possessions_per_min": 3.1,
        "baseline_possessions_per_min": 3.0,
    }

    print(json.dumps(evaluate_live_game(sample_payload), indent=2))

