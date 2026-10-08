"""Edge math shared by the recommendation engine, the bridge and the backtests.

Ideas taken from the betting literature (see BOOKS.md):
  * remove the bookmaker's margin before comparing prices (Buchdahl: proportional and power methods)
  * the closing market is a strong forecaster: anchor the model to the no-vig market and only bet the
    difference (Miller & Davidow, The Logic of Sports Betting; Buchdahl, Squares and Sharps)
  * stake with fractional Kelly and a hard cap (Kelly; Yao, Weighing the Odds; Peta, Trading Bases)
  * judge results by closing-line value and statistical significance, not win rate alone (Buchdahl)
  * shop every line: the best available price is part of the edge (Wong, Sharp Sports Betting)
"""

import math

import numpy as np


def implied(price: float) -> float:
    """Raw implied probability of American odds (includes the vig)."""
    price = float(price)
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def payout(price: float) -> float:
    """Profit per 1 unit staked at American odds."""
    price = float(price)
    return price / 100 if price > 0 else 100 / -price


def to_american(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def devig(prices: list[float], method: str = "power") -> list[float]:
    """Fair probabilities from a two- or three-way market.
    proportional: divide by the overround. power: raise each to k so they sum to 1, which takes more
    margin off longshots (closer to how books actually shade prices)."""
    raw = np.array([implied(p) for p in prices], float)
    if method == "proportional" or raw.sum() <= 1:
        return list(raw / raw.sum())
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if (raw ** k).sum() > 1:
            lo = k
        else:
            hi = k
    fair = raw ** ((lo + hi) / 2)
    return list(fair / fair.sum())


def blend(p_model: float, p_market: float, w_model: float) -> float:
    """Logit-space blend; w_model = 0 trusts the market, 1 trusts the model."""
    if p_market is None or (isinstance(p_market, float) and math.isnan(p_market)):
        return p_model
    lg = lambda p: math.log(min(max(p, 1e-6), 1 - 1e-6) / (1 - min(max(p, 1e-6), 1 - 1e-6)))
    z = w_model * lg(p_model) + (1 - w_model) * lg(p_market)
    return 1 / (1 + math.exp(-z))


def edge(p: float, price: float) -> float:
    """Expected profit per unit staked."""
    return p * payout(price) - (1 - p)


def kelly(p: float, price: float, fraction: float = 0.25, cap: float = 0.02) -> float:
    b = payout(price)
    return float(min(max((p * b - (1 - p)) / b, 0.0) * fraction, cap))


def significance(wins: int, n: int, avg_price: float) -> float:
    """One-sided p-value that a record this good comes from a bettor with no edge at these prices."""
    if n == 0:
        return 1.0
    be = implied(avg_price)
    z = (wins - n * be) / math.sqrt(n * be * (1 - be))
    return 0.5 * math.erfc(z / math.sqrt(2))


def clv(bet_price: float, close_prices: list[float], side: int = 0) -> float:
    """Closing-line value: how much better the bet price was than the no-vig closing probability."""
    fair_close = devig(close_prices)[side]
    return fair_close * payout(bet_price) - (1 - fair_close)
