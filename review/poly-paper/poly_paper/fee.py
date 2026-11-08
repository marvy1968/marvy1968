"""Unchanged Polymarket paper fee and integer contract formula.

baseline runner.py / pm_fill.py:
  THETA = 0.0695
  fee = round(THETA * contracts * price * (1 - price), 4)
  contracts = int(dollars // (price + THETA * price * (1 - price)))
  cost = round(contracts * price + fee, 4)

The authorization boundary checks a supplied fill against this formula. It does
not choose stake, edge, or contract count.
"""
from __future__ import annotations

THETA = 0.0695


def fee(contracts: int, price: float) -> float:
    return round(THETA * contracts * price * (1 - price), 4)


def cost(contracts: int, price: float, fee_value: float) -> float:
    return round(contracts * price + fee_value, 4)


def contracts_from_dollars(dollars: float, price: float) -> int:
    return int(dollars // (price + THETA * price * (1 - price)))


def economics_match(contracts, price, fee_value, cost_value) -> bool:
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts <= 0:
        return False
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return False
    if isinstance(fee_value, bool) or not isinstance(fee_value, (int, float)):
        return False
    if isinstance(cost_value, bool) or not isinstance(cost_value, (int, float)):
        return False
    if not 0 < float(price) < 1:
        return False
    expected_fee = fee(contracts, float(price))
    expected_cost = cost(contracts, float(price), expected_fee)
    return fee_value == expected_fee and cost_value == expected_cost
