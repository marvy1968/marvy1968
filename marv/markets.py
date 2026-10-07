"""Odds math and settlement from simulated score distributions."""

import numpy as np


def american_to_decimal(odds: float) -> float:
    return 1 + (odds / 100 if odds > 0 else 100 / -odds)


def american_to_prob(odds: float) -> float:
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def prob_to_american(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def no_vig(*odds: float) -> list[float]:
    """Fair probabilities from a set of American prices for mutually exclusive outcomes."""
    raw = [american_to_prob(o) for o in odds]
    total = sum(raw)
    return [r / total for r in raw]


def settle(diff: np.ndarray, line: float) -> np.ndarray:
    """Per-simulation return multiplier for a handicap bet: 1 win, 0 push, -1 loss, halves for quarter lines.

    diff is (our side's score minus the opponent's) for spreads, or (total - 0) for overs;
    the bet wins when diff + line > 0. Quarter lines (e.g. -0.25) split the stake across two lines.
    """
    if abs((line * 4) % 2) == 1:  # quarter line
        return (settle(diff, line - 0.25) + settle(diff, line + 0.25)) / 2
    return np.sign(diff + line)


def evaluate(outcome: np.ndarray, price: float) -> tuple[float, float, float]:
    """(win probability excluding pushes, EV per unit, edge in probability points).

    outcome holds per-simulation results from settle(): 1 win, -1 loss, 0 push, +-0.5 half results.
    """
    b = american_to_decimal(price) - 1
    win = np.clip(outcome, 0, 1).mean()
    loss = np.clip(-outcome, 0, 1).mean()
    ev = win * b - loss
    decided = win + loss
    prob = win / decided if decided else 0.5
    return float(prob), float(ev), float(ev / (1 + b))
