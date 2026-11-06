"""Bounded EWMA of realized R. Size only. Signals, stops, and risk limits stay put.

Weight may sit between ``lo`` and ``hi`` for the record. Dollars at risk never
exceed the configured risk percent: a weight above 1 does not increase size.
"""

from __future__ import annotations

from .config import WeightsCfg


def update_ewma(stats: dict, expert: str, realized_r: float, cfg: WeightsCfg) -> dict:
    row = dict(stats.get(expert) or {"ewma": 0.0, "n": 0})
    row["n"] = int(row.get("n", 0)) + 1
    prev = float(row.get("ewma", 0.0))
    row["ewma"] = (1.0 - cfg.alpha) * prev + cfg.alpha * float(realized_r)
    stats[expert] = row
    return stats


def recorded_weight(stats: dict, expert: str, cfg: WeightsCfg) -> float:
    row = stats.get(expert) or {}
    if int(row.get("n", 0)) < cfg.min_sample:
        return 1.0
    raw = 1.0 + max(-0.5, min(0.5, float(row.get("ewma", 0.0))))
    return max(cfg.lo, min(cfg.hi, raw))


def risk_dollars(equity: float, risk_pct: float, weight: float, governor: float) -> float:
    """Configured risk_pct is a ceiling. Governor and sub-1 weights only reduce it."""
    return equity * risk_pct * min(float(weight), 1.0) * float(governor)
