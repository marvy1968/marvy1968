#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
        return float(v)
    except Exception:
        return default


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(x, high))


def adaptive_kelly(signal: dict, bankroll: float = 10000.0) -> float:
    base_kelly = _to_float(signal.get("kelly_quarter", 0.02), 0.02)
    confidence = _to_float(signal.get("confidence", 0.5), 0.5)
    edge = _to_float(signal.get("edge", 0.0), 0.0)
    volatility = _to_float(signal.get("volatility", 0.5), 0.5)
    minutes_left = _to_float(signal.get("minutes_left", 99), 99)

    if confidence >= 0.90:
        confidence_factor = 1.25
    elif confidence >= 0.80:
        confidence_factor = 1.10
    elif confidence >= 0.70:
        confidence_factor = 1.00
    else:
        confidence_factor = 0.75

    if edge >= 0.10:
        edge_factor = 1.20
    elif edge >= 0.05:
        edge_factor = 1.05
    else:
        edge_factor = 0.85

    if volatility > 0.7:
        volatility_factor = 0.70
    elif volatility > 0.4:
        volatility_factor = 0.85
    else:
        volatility_factor = 1.00

    if minutes_left <= 2:
        late_game_factor = 0.70
    elif minutes_left <= 5:
        late_game_factor = 0.85
    else:
        late_game_factor = 1.00

    final_kelly = (
        base_kelly
        * confidence_factor
        * edge_factor
        * volatility_factor
        * late_game_factor
    )

    final_kelly = _clamp(final_kelly, 0.0, 0.10)
    return round(final_kelly, 4)


def calculate_bet_size(signal: dict, bankroll: float) -> float:
    kelly = adaptive_kelly(signal, bankroll)
    return round(bankroll * kelly, 2)


if __name__ == "__main__":
    test_signal = {
        "kelly_quarter": 0.06,
        "confidence": 0.88,
        "edge": 0.08,
        "volatility": 0.3,
        "minutes_left": 6,
    }

    print("Adaptive Kelly:", adaptive_kelly(test_signal))
    print("Bet Size ($):", calculate_bet_size(test_signal, 1000))
