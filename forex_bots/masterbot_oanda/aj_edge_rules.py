#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def is_aj_edge(signal: Dict[str, Any]) -> bool:
    minutes_left = _to_float(signal.get("minutes_left", 999))
    leader_prob = _to_float(signal.get("leader_win_prob", 0))
    edge = _to_float(signal.get("display_edge", signal.get("edge", 0)))
    ev = _to_float(signal.get("ev_percent", 0))
    score_diff = _to_float(signal.get("score_diff", 0))

    pace = str(signal.get("pace", "") or "").lower()
    pressure = str(signal.get("pressure", "") or "").lower()
    suspicious = bool(signal.get("suspicious", False))

    return (
        minutes_left <= 6
        and leader_prob >= 0.85
        and edge >= 22
        and ev >= 35
        and score_diff >= 12
        and pace != "foul_chaos"
        and pressure != "high"
        and not suspicious
    )


def get_aj_reason(signal: Dict[str, Any]) -> str:
    reasons = []

    if _to_float(signal.get("minutes_left", 999)) <= 6:
        reasons.append("late_game")

    if _to_float(signal.get("leader_win_prob", 0)) >= 0.85:
        reasons.append("strong_leader")

    if _to_float(signal.get("display_edge", signal.get("edge", 0))) >= 22:
        reasons.append("high_edge")

    if _to_float(signal.get("ev_percent", 0)) >= 35:
        reasons.append("high_ev")

    if _to_float(signal.get("score_diff", 0)) >= 12:
        reasons.append("blowout_control")

    if str(signal.get("pace", "")).lower() != "foul_chaos":
        reasons.append("stable_pace")

    if str(signal.get("pressure", "")).lower() != "high":
        reasons.append("low_pressure")

    if not bool(signal.get("suspicious", False)):
        reasons.append("clean_signal")

    return ", ".join(reasons)
