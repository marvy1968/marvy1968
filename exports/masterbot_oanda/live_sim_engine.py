#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

import numpy as np

from pace_utils import build_ot_means


def _to_float(v: Any, default: float = 0.0) -> float:
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


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(x, high))


def _total_remaining_seconds(seconds_remaining: int, mode: str) -> int:
    mode = str(mode or "").lower()
    seconds_remaining = max(0, int(seconds_remaining))

    if mode == "1h":
        return seconds_remaining + 1200
    return seconds_remaining


def _progress(total_remaining_seconds: int, total_game_seconds: int = 2400) -> float:
    elapsed = total_game_seconds - max(0, total_remaining_seconds)
    return _clamp(elapsed / max(1, total_game_seconds), 0.0, 1.0)


def _remaining_means(
    *,
    current_home_score: float,
    current_away_score: float,
    total_remaining_seconds: int,
    model_total: float,
    model_margin: float,
    market_total: float,
    total_game_seconds: int = 2400,
) -> dict[str, float]:
    expected_home_final = max(0.0, (model_total + model_margin) / 2.0)
    expected_away_final = max(0.0, (model_total - model_margin) / 2.0)

    model_home_remaining = max(0.0, expected_home_final - current_home_score)
    model_away_remaining = max(0.0, expected_away_final - current_away_score)

    current_total = current_home_score + current_away_score
    market_remaining_total = max(0.0, market_total - current_total)

    total_expected = max(1e-9, expected_home_final + expected_away_final)
    home_share = expected_home_final / total_expected
    away_share = expected_away_final / total_expected

    market_home_remaining = market_remaining_total * home_share
    market_away_remaining = market_remaining_total * away_share

    prog = _progress(total_remaining_seconds, total_game_seconds)

    market_weight = _clamp(0.78 - (prog * 0.38), 0.35, 0.80)
    model_weight = 1.0 - market_weight

    home_remaining_mean = (model_home_remaining * model_weight) + (market_home_remaining * market_weight)
    away_remaining_mean = (model_away_remaining * model_weight) + (market_away_remaining * market_weight)

    if prog < 0.50:
        home_remaining_mean = max(home_remaining_mean, market_home_remaining * 0.75)
        away_remaining_mean = max(away_remaining_mean, market_away_remaining * 0.75)

    return {
        "home_remaining_mean": max(0.0, home_remaining_mean),
        "away_remaining_mean": max(0.0, away_remaining_mean),
        "expected_home_final": expected_home_final,
        "expected_away_final": expected_away_final,
        "market_weight": market_weight,
        "model_weight": model_weight,
        "progress": prog,
    }


def _apply_variance_scaling(
    *,
    home_remaining_mean: float,
    away_remaining_mean: float,
    progress: float,
) -> tuple[float, float]:
    if progress < 0.20:
        home_remaining_mean *= 1.08
        away_remaining_mean *= 1.08
    elif progress < 0.40:
        home_remaining_mean *= 1.05
        away_remaining_mean *= 1.05

    return max(0.0, home_remaining_mean), max(0.0, away_remaining_mean)


def simulate_game_vectorized(
    current_home_score: float,
    current_away_score: float,
    seconds_remaining: int,
    model_total: float,
    model_margin: float,
    home_spread_line: float,
    market_total: float,
    n_sims: int = 10000,
    seed: int | None = None,
    mode: str = "2h",
) -> Dict[str, float]:
    rng = np.random.default_rng(seed)

    current_home_score = _to_float(current_home_score, 0.0)
    current_away_score = _to_float(current_away_score, 0.0)
    seconds_remaining = int(_to_float(seconds_remaining, 0.0))
    model_total = _to_float(model_total, 0.0)
    model_margin = _to_float(model_margin, 0.0)
    home_spread_line = _to_float(home_spread_line, 0.0)
    market_total = _to_float(market_total, 0.0)

    total_game_seconds = 2400
    total_remaining_seconds = _total_remaining_seconds(seconds_remaining, mode)

    means = _remaining_means(
        current_home_score=current_home_score,
        current_away_score=current_away_score,
        total_remaining_seconds=total_remaining_seconds,
        model_total=model_total,
        model_margin=model_margin,
        market_total=market_total,
        total_game_seconds=total_game_seconds,
    )

    home_remaining_mean, away_remaining_mean = _apply_variance_scaling(
        home_remaining_mean=means["home_remaining_mean"],
        away_remaining_mean=means["away_remaining_mean"],
        progress=means["progress"],
    )

    sim_home_remaining = rng.poisson(home_remaining_mean, n_sims)
    sim_away_remaining = rng.poisson(away_remaining_mean, n_sims)

    final_home = current_home_score + sim_home_remaining
    final_away = current_away_score + sim_away_remaining

    ot = build_ot_means(
        current_home_score=current_home_score,
        current_away_score=current_away_score,
        seconds_remaining=total_remaining_seconds,
        model_total=model_total,
        model_margin=model_margin,
        total_game_seconds=total_game_seconds,
    )

    ot_home_mean = max(0.2, _to_float(ot["ot_home_mean"], 0.2))
    ot_away_mean = max(0.2, _to_float(ot["ot_away_mean"], 0.2))

    ties = final_home == final_away
    while np.any(ties):
        n_ties = int(ties.sum())
        ot_home = rng.poisson(ot_home_mean, n_ties)
        ot_away = rng.poisson(ot_away_mean, n_ties)

        final_home[ties] += ot_home
        final_away[ties] += ot_away

        ties = final_home == final_away

    final_total = final_home + final_away
    final_margin = final_home - final_away

    spread_result = final_margin + home_spread_line
    home_cover = spread_result > 0
    away_cover = spread_result < 0
    push_spread = spread_result == 0

    over = final_total > market_total
    under = final_total < market_total
    push_total = final_total == market_total

    home_win = final_home > final_away
    away_win = final_away > final_home

    return {
        "home_cover_prob": float(home_cover.mean()),
        "away_cover_prob": float(away_cover.mean()),
        "push_spread_prob": float(push_spread.mean()),
        "over_prob": float(over.mean()),
        "under_prob": float(under.mean()),
        "push_total_prob": float(push_total.mean()),
        "home_win_prob": float(home_win.mean()),
        "away_win_prob": float(away_win.mean()),
        "ot_home_mean": float(ot_home_mean),
        "ot_away_mean": float(ot_away_mean),
        "home_remaining_mean": float(home_remaining_mean),
        "away_remaining_mean": float(away_remaining_mean),
        "market_weight": float(means["market_weight"]),
        "model_weight": float(means["model_weight"]),
        "progress": float(means["progress"]),
    }


if __name__ == "__main__":
    result = simulate_game_vectorized(
        current_home_score=20,
        current_away_score=4,
        seconds_remaining=(11 * 60) + 58,
        model_total=121.89,
        model_margin=18.79,
        home_spread_line=-17.0,
        market_total=135.5,
        n_sims=10000,
        seed=42,
        mode="1h",
    )

    print("LIVE SIM ENGINE INTEGRATED TEST")
    for k, v in result.items():
        print(f"{k}: {v}")
