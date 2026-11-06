"""Sizing, loss stops, drawdown governor, and the sector firewall.

The governor changes an exposure multiplier. It does not rewrite the configured
risk percents. Expert weight is applied later and cannot raise risk above the
configured percent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import RiskCfg


def loss_stopped(realized: float, start_equity: float, limit_pct: float) -> bool:
    if start_equity <= 0:
        return True
    return realized <= -abs(limit_pct) * start_equity


def snap_mult(mult: float) -> float:
    for tier in (0.15, 0.50, 0.85, 1.0):
        if abs(mult - tier) < 1e-6:
            return tier
    if mult < 0.50:
        return 0.15
    if mult < 0.85:
        return 0.50
    if mult < 1.0:
        return 0.85
    return 1.0


def tier_for_enter(drawdown: float) -> float:
    if drawdown >= 0.125:
        return 0.15
    if drawdown >= 0.085:
        return 0.50
    if drawdown >= 0.05:
        return 0.85
    return 1.0


def governor_step(current: float, drawdown: float) -> float:
    """Tighten immediately. Loosen one tier at a time, and only past the exit gap.

    Enter 0.85 at 5% (exit back through 3.5%), 0.50 at 8.5% (exit 7.0%),
    0.15 at 12.5% (exit 11.0%).
    """
    current = snap_mult(current)
    entered = tier_for_enter(drawdown)
    if entered < current - 1e-9:
        return entered
    if current <= 0.15:
        return 0.15 if drawdown > 0.110 else 0.50
    if current <= 0.50:
        return 0.50 if drawdown > 0.070 else 0.85
    if current <= 0.85:
        return 0.85 if drawdown > 0.035 else 1.0
    return 1.0


def update_governor(equity: float, peak: float, current_mult: float) -> tuple[float, float, float]:
    peak = max(peak, equity)
    drawdown = (peak - equity) / peak if peak > 0 else 1.0
    return peak, drawdown, governor_step(current_mult, drawdown)


def mode_killed(mode_pnl: float, mode_peak: float, start_equity: float, limit_pct: float) -> bool:
    if start_equity <= 0:
        return True
    peak = max(mode_peak, mode_pnl)
    drawdown = (peak - mode_pnl) / start_equity
    return drawdown >= limit_pct


@dataclass
class ExposureItem:
    symbol: str
    side: str
    qty: float
    price: float
    stop: float | None
    sector: str


def measure_risk(item: ExposureItem) -> float | None:
    if item.stop is None or item.qty <= 0 or item.price <= 0:
        return None
    return abs(item.price - item.stop) * item.qty


def exposure_ok(current: list[ExposureItem], new: ExposureItem, equity: float, cfg: RiskCfg) -> tuple[bool, str]:
    """Caps are checked on current positions PLUS the new order."""
    if equity <= 0:
        return False, "equity"
    rows = list(current) + [new]
    if len(rows) > cfg.max_positions:
        return False, "count"
    per_sector = int(getattr(cfg, "max_per_sector", 1) or 1)
    if sum(1 for row in rows if row.sector == new.sector) > per_sector:
        return False, "sector"
    if any(row.symbol == new.symbol for row in current):
        return False, "symbol_held"
    gross = 0.0
    net = 0.0
    for row in rows:
        notional = abs(row.qty * row.price)
        gross += notional
        sign = 1.0 if row.side == "long" else -1.0
        net += sign * notional
        risk = measure_risk(row)
        if risk is None:
            return False, "unmeasured_risk"
    if gross > cfg.max_gross_frac * equity + 1e-6:
        return False, "gross"
    if abs(net) > cfg.max_net_frac * equity + 1e-6:
        return False, "net"
    symbol_notional = abs(new.qty * new.price)
    if symbol_notional > cfg.max_symbol_frac * equity + 1e-6:
        return False, "symbol"
    total_risk = 0.0
    for row in rows:
        risk = measure_risk(row)
        if risk is None:
            return False, "unmeasured_risk"
        total_risk += risk
    if total_risk > cfg.gross_open_risk_frac * equity + 1e-6:
        return False, "open_risk"
    return True, "ok"


def share_qty(equity: float, price: float, stop: float, risk_dollars: float, max_symbol_frac: float) -> int:
    distance = abs(price - stop)
    if equity <= 0 or price <= 0 or distance <= 0 or risk_dollars <= 0:
        return 0
    raw = int(risk_dollars // distance)
    cap = int((equity * max_symbol_frac) // price)
    return max(0, min(raw, cap))
