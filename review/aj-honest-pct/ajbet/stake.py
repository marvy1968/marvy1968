"""Stake size.

Full Kelly on a decimal price. The fraction uses the probability the caller
passes. ``value_scan`` passes ``p_final`` when ``AJ_HONEST_PCT`` is on, and
the raw pacer probability when the flag is off.
"""

from __future__ import annotations


def kelly_fraction(probability: float, decimal_odds: float) -> float:
    """Full Kelly fraction of the bankroll. Never negative."""
    if decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - probability
    fraction = (b * probability - q) / b
    if fraction < 0.0:
        return 0.0
    return fraction
