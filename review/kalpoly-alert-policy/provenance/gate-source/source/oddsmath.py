"""Odds conversions, de-vig, and stake helpers (American odds)."""
from __future__ import annotations

import math
from typing import Iterable, List


def american_to_prob(odds: float) -> float:
    """Implied probability (with vig) of American odds."""
    odds = float(odds)
    if odds == 0 or -100 < odds < 100:
        raise ValueError(f"invalid American odds: {odds}")
    if odds < 0:
        return -odds / (-odds + 100.0)
    return 100.0 / (odds + 100.0)


def prob_to_american(p: float) -> float:
    """Fair American odds for probability p (0<p<1)."""
    if not 0 < p < 1:
        raise ValueError(f"probability out of range: {p}")
    if p >= 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


def valid_american(odds) -> bool:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return False
    return o <= -100 or o >= 100


def median_american(xs: Iterable[float]):
    """Median of American prices taken in implied-probability space (fix 2026-10-02: a plain median of
    [-102, +100] gave -1, an impossible price that crashed the line swing scan). Invalid prices ignored."""
    ps = sorted(american_to_prob(x) for x in xs if valid_american(x))
    if not ps:
        return None
    n = len(ps)
    p = ps[n // 2] if n % 2 else (ps[n // 2 - 1] + ps[n // 2]) / 2.0
    a = prob_to_american(p)
    if -100 < a < 100:                      # p == 0.5 exactly -> even money
        a = 100.0
    return float(round(a))


def american_to_decimal(odds: float) -> float:
    odds = float(odds)
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / -odds)


def devig(probs: Iterable[float]) -> List[float]:
    """Multiplicative (proportional) de-vig: scale implied probs to sum to 1."""
    probs = list(probs)
    s = sum(probs)
    if s <= 0:
        raise ValueError("probabilities must sum > 0")
    return [p / s for p in probs]


def devig_power(probs: Iterable[float]) -> List[float]:
    """Power de-vig: find k with sum(p_i^k) = 1. Removes more margin from longshots than the
    multiplicative method (counters the favourite-longshot bias)."""
    probs = list(probs)
    if sum(probs) <= 1.0 + 1e-12:
        return devig(probs)
    lo, hi = 1.0, 10.0
    for _ in range(100):
        k = (lo + hi) / 2
        if sum(p ** k for p in probs) > 1.0:
            lo = k
        else:
            hi = k
    out = [p ** ((lo + hi) / 2) for p in probs]
    s = sum(out)
    return [x / s for x in out]


def devig_american(odds: Iterable[float], method: str = "multiplicative") -> List[float]:
    probs = [american_to_prob(o) for o in odds]
    return devig_power(probs) if method == "power" else devig(probs)


def american_points(odds: float) -> float:
    """Map American odds onto a continuous scale (-100 == +100 == 0).

    Used to measure 'ML improved by N points': -1300 -> -300 is 1000 points,
    -150 -> +200 is 150 points.
    """
    odds = float(odds)
    return odds + 100.0 if odds < 0 else odds - 100.0


def ml_move_points(pregame: float, live: float) -> float:
    """Positive = live price is longer (better for backing the favorite)."""
    return american_points(live) - american_points(pregame)


def stake_to_win(odds: float, to_win: float) -> float:
    """Risk required to win `to_win` at American `odds`."""
    return to_win / (american_to_decimal(odds) - 1.0)


def profit(odds: float, stake: float) -> float:
    """Net profit on a winning bet of `stake` at American `odds`."""
    return stake * (american_to_decimal(odds) - 1.0)


def fmt_american(odds: float | None) -> str:
    if odds is None or (isinstance(odds, float) and math.isnan(odds)):
        return "n/a"
    o = round(odds)
    return f"+{o}" if o > 0 else f"{o}"
