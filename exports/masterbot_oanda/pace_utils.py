#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict


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


def compute_live_scoring_rates(
    current_home_score: float,
    current_away_score: float,
    seconds_remaining: int,
    total_game_seconds: int = 2400,
) -> Dict[str, float]:
    """
    Computes current live scoring rates in points per second.

    Returns:
      {
        "home_rate": ...,
        "away_rate": ...,
        "combined_rate": ...,
        "seconds_elapsed": ...,
      }
    """
    current_home_score = _to_float(current_home_score, 0.0)
    current_away_score = _to_float(current_away_score, 0.0)
    seconds_remaining = int(_to_float(seconds_remaining, 0.0))
    total_game_seconds = int(_to_float(total_game_seconds, 2400.0))

    seconds_elapsed = max(1, total_game_seconds - max(0, seconds_remaining))

    home_rate = current_home_score / seconds_elapsed
    away_rate = current_away_score / seconds_elapsed
    combined_rate = (current_home_score + current_away_score) / seconds_elapsed

    return {
        "home_rate": home_rate,
        "away_rate": away_rate,
        "combined_rate": combined_rate,
        "seconds_elapsed": float(seconds_elapsed),
    }


def compute_model_scoring_rates(
    model_total: float,
    model_margin: float,
    total_game_seconds: int = 2400,
) -> Dict[str, float]:
    """
    Converts model total + margin into implied home/away scoring rates.

    expected_home_final = (model_total + model_margin) / 2
    expected_away_final = (model_total - model_margin) / 2
    """
    model_total = _to_float(model_total, 0.0)
    model_margin = _to_float(model_margin, 0.0)
    total_game_seconds = int(_to_float(total_game_seconds, 2400.0))

    expected_home_final = max(0.0, (model_total + model_margin) / 2.0)
    expected_away_final = max(0.0, (model_total - model_margin) / 2.0)

    home_rate = expected_home_final / max(1, total_game_seconds)
    away_rate = expected_away_final / max(1, total_game_seconds)
    combined_rate = (expected_home_final + expected_away_final) / max(1, total_game_seconds)

    return {
        "expected_home_final": expected_home_final,
        "expected_away_final": expected_away_final,
        "home_rate": home_rate,
        "away_rate": away_rate,
        "combined_rate": combined_rate,
    }


def blend_live_and_model_rates(
    *,
    live_home_rate: float,
    live_away_rate: float,
    model_home_rate: float,
    model_away_rate: float,
    seconds_remaining: int,
    total_game_seconds: int = 2400,
) -> Dict[str, float]:
    """
    Blends live pace with model pace.

    More time left  -> trust model more
    Less time left  -> trust live pace more
    """
    live_home_rate = _to_float(live_home_rate, 0.0)
    live_away_rate = _to_float(live_away_rate, 0.0)
    model_home_rate = _to_float(model_home_rate, 0.0)
    model_away_rate = _to_float(model_away_rate, 0.0)
    seconds_remaining = int(_to_float(seconds_remaining, 0.0))
    total_game_seconds = int(_to_float(total_game_seconds, 2400.0))

    progress = _clamp(
        (total_game_seconds - max(0, seconds_remaining)) / max(1, total_game_seconds),
        0.0,
        1.0,
    )

    live_weight = _clamp(0.25 + (progress * 0.60), 0.25, 0.85)
    model_weight = 1.0 - live_weight

    blended_home_rate = (live_home_rate * live_weight) + (model_home_rate * model_weight)
    blended_away_rate = (live_away_rate * live_weight) + (model_away_rate * model_weight)

    return {
        "progress": progress,
        "live_weight": live_weight,
        "model_weight": model_weight,
        "home_rate": blended_home_rate,
        "away_rate": blended_away_rate,
        "combined_rate": blended_home_rate + blended_away_rate,
    }


def compute_ot_means_from_pace(
    *,
    home_rate: float,
    away_rate: float,
    ot_seconds: int = 300,
    min_mean: float = 0.2,
) -> tuple[float, float]:
    """
    Converts per-second rates into OT scoring means.
    NCAA OT = 5 minutes = 300 seconds.
    """
    home_rate = _to_float(home_rate, 0.0)
    away_rate = _to_float(away_rate, 0.0)
    ot_seconds = int(_to_float(ot_seconds, 300.0))
    min_mean = _to_float(min_mean, 0.2)

    home_ot_mean = max(min_mean, home_rate * ot_seconds)
    away_ot_mean = max(min_mean, away_rate * ot_seconds)

    return home_ot_mean, away_ot_mean


def build_ot_means(
    *,
    current_home_score: float,
    current_away_score: float,
    seconds_remaining: int,
    model_total: float,
    model_margin: float,
    total_game_seconds: int = 2400,
    ot_seconds: int = 300,
) -> Dict[str, float]:
    """
    Full helper:
    1. compute live pace
    2. compute model pace
    3. blend them
    4. convert blended pace into OT means
    """
    live = compute_live_scoring_rates(
        current_home_score=current_home_score,
        current_away_score=current_away_score,
        seconds_remaining=seconds_remaining,
        total_game_seconds=total_game_seconds,
    )

    model = compute_model_scoring_rates(
        model_total=model_total,
        model_margin=model_margin,
        total_game_seconds=total_game_seconds,
    )

    blended = blend_live_and_model_rates(
        live_home_rate=live["home_rate"],
        live_away_rate=live["away_rate"],
        model_home_rate=model["home_rate"],
        model_away_rate=model["away_rate"],
        seconds_remaining=seconds_remaining,
        total_game_seconds=total_game_seconds,
    )

    home_ot_mean, away_ot_mean = compute_ot_means_from_pace(
        home_rate=blended["home_rate"],
        away_rate=blended["away_rate"],
        ot_seconds=ot_seconds,
    )

    return {
        "live_home_rate": live["home_rate"],
        "live_away_rate": live["away_rate"],
        "model_home_rate": model["home_rate"],
        "model_away_rate": model["away_rate"],
        "progress": blended["progress"],
        "live_weight": blended["live_weight"],
        "model_weight": blended["model_weight"],
        "blended_home_rate": blended["home_rate"],
        "blended_away_rate": blended["away_rate"],
        "ot_home_mean": home_ot_mean,
        "ot_away_mean": away_ot_mean,
    }


if __name__ == "__main__":
    print("PACE UTILS TEST")

    live = compute_live_scoring_rates(
        current_home_score=71,
        current_away_score=73,
        seconds_remaining=305,
    )
    print("live:", live)

    model = compute_model_scoring_rates(
        model_total=167.3,
        model_margin=1.8,
    )
    print("model:", model)

    blended = blend_live_and_model_rates(
        live_home_rate=live["home_rate"],
        live_away_rate=live["away_rate"],
        model_home_rate=model["home_rate"],
        model_away_rate=model["away_rate"],
        seconds_remaining=305,
    )
    print("blended:", blended)

    ot = build_ot_means(
        current_home_score=71,
        current_away_score=73,
        seconds_remaining=305,
        model_total=167.3,
        model_margin=1.8,
    )
    print("ot:", ot)
