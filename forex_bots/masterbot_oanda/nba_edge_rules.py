#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def is_aj_edge(sig: dict[str, Any]) -> bool:
    """
    NBA-specific AJ rules.
    Stricter than college because NBA late-game variance and 3PT swings are higher.
    """
    try:
        period = int(sig.get("period", 0) or 0)
        minutes_left = _f(sig.get("minutes_left", 999))
        score_diff = abs(_f(sig.get("score_diff", 0)))
        win_prob = _f(sig.get("leader_win_prob", 0))
        edge = _f(sig.get("display_edge", sig.get("edge", 0)))
        pace = str(sig.get("pace", "") or "").lower()
        pressure = str(sig.get("pressure", "") or "").lower()

        in_closing_window = period >= 4 and minutes_left <= 5.0
        has_safe_lead = score_diff >= 17
        has_lock_prob = win_prob >= 0.92
        has_massive_edge = edge >= 25.0
        is_stable = pace not in ["foul_chaos", "extreme"] and pressure != "high"

        return all([in_closing_window, has_safe_lead, has_lock_prob, has_massive_edge, is_stable])

    except Exception:
        return False


def get_aj_reason(sig: dict[str, Any]) -> str:
    reasons: list[str] = []

    if int(sig.get("period", 0) or 0) >= 4:
        reasons.append("nba_4q")
    if _f(sig.get("minutes_left", 999)) <= 5.0:
        reasons.append("nba_closing_window")
    if abs(_f(sig.get("score_diff", 0))) >= 17:
        reasons.append("pro_blowout")
    if _f(sig.get("leader_win_prob", 0)) >= 0.92:
        reasons.append("stat_lock")
    if _f(sig.get("display_edge", sig.get("edge", 0))) >= 25.0:
        reasons.append("massive_edge")

    return ", ".join(reasons)


def get_nba_bet_multiplier(sig: dict[str, Any]) -> float:
    """
    Shrink bet size in likely garbage-time conditions.
    """
    minutes_left = _f(sig.get("minutes_left", 999))
    score_diff = abs(_f(sig.get("score_diff", 0)))

    if minutes_left <= 3.5 and score_diff >= 22:
        return 0.5

    if minutes_left <= 5.0 and score_diff >= 17:
        return 0.75

    return 1.0


def is_nba_hot_streak(current_gs: dict[str, Any], last_gs: dict[str, Any] | None) -> bool:
    """
    If either team scored 10+ points since last poll, treat as a hot streak.
    """
    if not last_gs:
        return False

    try:
        home_delta = int(current_gs.get("home_score", 0) or 0) - int(last_gs.get("home_score", 0) or 0)
        away_delta = int(current_gs.get("away_score", 0) or 0) - int(last_gs.get("away_score", 0) or 0)
        return home_delta >= 10 or away_delta >= 10
    except Exception:
        return False
