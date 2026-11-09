"""SPY regime: 50/200 SMA, 20-day slope, ATR percent band.

UP and DOWN are trend. Anything else, including a volatility extreme, is CHOP.
Live trend entries are flat in chop. Range-box is the chop module and is log-only.
"""

from __future__ import annotations

from .config import RegimeCfg
from .indicators import atr, sma
from .types import Bar, Regime


def classify_regime(bars: list[Bar], cfg: RegimeCfg | None = None) -> Regime:
    cfg = cfg or RegimeCfg()
    closes = [b.close for b in bars]
    need = max(cfg.sma_slow, cfg.slope_days) + 1
    if len(bars) < need:
        return Regime("chop", reason="insufficient_history")
    fast = sma(closes, cfg.sma_fast)
    slow = sma(closes, cfg.sma_slow)
    if fast is None or slow is None:
        return Regime("chop", reason="insufficient_history")
    slope = closes[-1] / closes[-1 - cfg.slope_days] - 1.0
    width = atr(bars, cfg.atr_period)
    atr_pct = (width / closes[-1]) if width and closes[-1] else None
    base = Regime("chop", sma_fast=fast, sma_slow=slow, slope=slope, atr_pct=atr_pct)
    if atr_pct is None or not (cfg.min_atr_pct <= atr_pct <= cfg.max_atr_pct):
        base.reason = "vol_filter"
        return base
    price = closes[-1]
    if price > fast > slow and slope >= cfg.min_slope:
        base.name = "up"
        base.reason = "trend_up"
        return base
    if price < fast < slow and slope <= -cfg.min_slope:
        base.name = "down"
        base.reason = "trend_down"
        return base
    base.reason = "no_trend"
    return base


def allows_side(regime: Regime, side: str) -> bool:
    if regime.name == "up":
        return side == "long"
    if regime.name == "down":
        return side == "short"
    return False
