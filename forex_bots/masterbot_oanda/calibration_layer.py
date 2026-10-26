#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from typing import Any


AGGRESSIVE_MODE = True

# Higher cap than your current 0.15 / 0.17 behavior
DEFAULT_EDGE_CAP = 0.22
PREGAME_EDGE_CAP = 0.20
LIVE_EDGE_CAP = 0.24

# Confidence floors / boosts
SUSPICIOUS_CONF_FLOOR = 0.82
NORMAL_CONF_BOOST = 0.03
SUSPICIOUS_CONF_PENALTY = 0.02


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _is_live(edge: dict[str, Any]) -> bool:
    period = int(_to_float(edge.get("period", 0), 0))
    minutes_left = int(_to_float(edge.get("minutes_left", 0), 0))
    seconds_left = int(_to_float(edge.get("seconds_left", 0), 0))
    return period > 0 or minutes_left > 0 or seconds_left > 0


def _edge_cap(edge: dict[str, Any]) -> float:
    if _is_live(edge):
        return LIVE_EDGE_CAP if AGGRESSIVE_MODE else DEFAULT_EDGE_CAP
    return PREGAME_EDGE_CAP if AGGRESSIVE_MODE else DEFAULT_EDGE_CAP


def _confidence_from_prob(prob: float, suspicious: bool) -> float:
    conf = prob

    if suspicious:
        conf = max(conf - SUSPICIOUS_CONF_PENALTY, SUSPICIOUS_CONF_FLOOR)
    else:
        conf = conf + NORMAL_CONF_BOOST

    return _clamp(conf, 0.50, 0.97)


def _calibrate_single(edge: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(edge)

    suspicious = bool(out.get("suspicious_any", False))
    cap = _edge_cap(out)

    # Cap edges a little higher so elite signals can actually exist
    for key in [
        "home_spread_edge",
        "away_spread_edge",
        "over_edge",
        "under_edge",
        "home_ml_edge",
        "away_ml_edge",
    ]:
        if key in out:
            out[key] = round(_clamp(_to_float(out.get(key, 0.0)), 0.0, cap), 4)

    out["dynamic_edge_cap"] = cap

    # Build practical confidences from probs
    home_cover_prob = _to_float(out.get("home_cover_prob", 0.0))
    away_cover_prob = _to_float(out.get("away_cover_prob", 0.0))
    over_prob = _to_float(out.get("over_prob", 0.0))
    under_prob = _to_float(out.get("under_prob", 0.0))
    home_ml_prob = _to_float(out.get("home_ml_prob", out.get("home_win_prob", 0.0)))
    away_ml_prob = _to_float(out.get("away_ml_prob", out.get("away_win_prob", 0.0)))

    out["home_cover_confidence"] = round(_confidence_from_prob(home_cover_prob, suspicious), 3)
    out["away_cover_confidence"] = round(_confidence_from_prob(away_cover_prob, suspicious), 3)
    out["over_confidence"] = round(_confidence_from_prob(over_prob, suspicious), 3)
    out["under_confidence"] = round(_confidence_from_prob(under_prob, suspicious), 3)
    out["home_ml_confidence"] = round(_confidence_from_prob(home_ml_prob, suspicious), 3)
    out["away_ml_confidence"] = round(_confidence_from_prob(away_ml_prob, suspicious), 3)

    return out


def calibrate_edge_output(payload: Any) -> Any:
    """
    Supports either:
    - single edge dict
    - list of edge dicts
    """
    if isinstance(payload, list):
        return [_calibrate_single(x) for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        return _calibrate_single(payload)

    return payload


if __name__ == "__main__":
    sample = {
        "game": "Duke Blue Devils @ LSU Tigers",
        "sport_key": "basketball_wncaab",
        "period": 0,
        "minutes_left": 0,
        "seconds_left": 0,
        "suspicious_any": True,
        "away_cover_prob": 0.7094,
        "under_prob": 1.0,
        "home_ml_prob": 0.6078,
        "away_ml_prob": 0.3922,
        "away_spread_edge": 0.15,
        "under_edge": 0.15,
        "home_ml_edge": 0.084,
    }

    out = calibrate_edge_output(sample)
    print("CALIBRATION TEST")
    print("dynamic_edge_cap:", out.get("dynamic_edge_cap"))
    print("away_cover_confidence:", out.get("away_cover_confidence"))
    print("under_confidence:", out.get("under_confidence"))
    print("home_ml_confidence:", out.get("home_ml_confidence"))
    print("away_spread_edge:", out.get("away_spread_edge"))
    print("under_edge:", out.get("under_edge"))
