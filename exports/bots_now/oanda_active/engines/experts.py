from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List
from .types import EngineContext, EngineSignal, clamp

# ---------------------------------------------------------
# Expert 1: Trend Breakout (Donchian / HH-LL continuation)
# ---------------------------------------------------------
@dataclass
class TrendBreakoutExpert:
    name: str = "trend_breakout"

    def evaluate(self, c: EngineContext) -> EngineSignal:
        if c.donH is None or c.donL is None:
            return EngineSignal(self.name, None, 0.0, reason="missing donchian")

        # Only favor in trend regime
        if c.regime != "trend":
            return EngineSignal(self.name, None, 0.15, reason="not trend regime")

        # breakout logic
        long_ok = c.price >= (c.donH + c.buf)
        short_ok = c.price <= (c.donL - c.buf)

        if long_ok and c.bias in ("neutral", "long"):
            conf = 0.55 + 0.01 * float(c.adx or 0)
            return EngineSignal(self.name, "long", clamp(conf, 0.0, 0.95), reason="donchian breakout long")
        if short_ok and c.bias in ("neutral", "short"):
            conf = 0.55 + 0.01 * float(c.adx or 0)
            return EngineSignal(self.name, "short", clamp(conf, 0.0, 0.95), reason="donchian breakout short")

        return EngineSignal(self.name, None, 0.20, reason="no breakout")

# ---------------------------------------------------------
# Expert 2: Range Mean Reversion (fade edges in balance)
# ---------------------------------------------------------
@dataclass
class MeanReversionExpert:
    name: str = "mean_reversion"

    def evaluate(self, c: EngineContext) -> EngineSignal:
        if c.donH is None or c.donL is None:
            return EngineSignal(self.name, None, 0.0, reason="missing donchian")

        if c.regime != "range":
            return EngineSignal(self.name, None, 0.15, reason="not range regime")

        mid = (c.donH + c.donL) / 2.0
        span = max(1e-9, (c.donH - c.donL))

        # how close to edges
        near_low = (c.price - c.donL) <= (0.18 * span)
        near_high = (c.donH - c.price) <= (0.18 * span)

        if near_low and c.bias in ("neutral", "long"):
            # more confident if ADX low (more rangy)
            adx = float(c.adx or 0.0)
            conf = 0.55 + clamp((22.0 - adx) / 50.0, 0.0, 0.25)
            return EngineSignal(self.name, "long", clamp(conf, 0.0, 0.85), reason="fade lower range edge")

        if near_high and c.bias in ("neutral", "short"):
            adx = float(c.adx or 0.0)
            conf = 0.55 + clamp((22.0 - adx) / 50.0, 0.0, 0.25)
            return EngineSignal(self.name, "short", clamp(conf, 0.0, 0.85), reason="fade upper range edge")

        return EngineSignal(self.name, None, 0.25, reason="no range edge")

# ---------------------------------------------------------
# Expert 3: Volatility Squeeze / Expansion (compression -> break)
# ---------------------------------------------------------
@dataclass
class SqueezeExpert:
    name: str = "squeeze_expansion"
    atr_floor: float = 1e-9

    def evaluate(self, c: EngineContext) -> EngineSignal:
        if c.atr is None or c.atr <= self.atr_floor:
            return EngineSignal(self.name, None, 0.0, block=True, reason="bad atr")

        # crude squeeze: ATR small vs price
        atr_pct = float(c.atr) / max(1e-9, float(c.price))
        squeeze = atr_pct < 0.00035  # tune for EURUSD

        if not squeeze:
            return EngineSignal(self.name, None, 0.20, reason="no squeeze")

        # if squeezing, prefer breakout direction if price is near a boundary
        if c.donH is not None and c.price >= (c.donH - 0.25 * (c.donH - (c.donL or c.donH))):
            return EngineSignal(self.name, "long", 0.60, reason="squeeze near high -> expansion long")
        if c.donL is not None and c.price <= (c.donL + 0.25 * ((c.donH or c.donL) - c.donL)):
            return EngineSignal(self.name, "short", 0.60, reason="squeeze near low -> expansion short")

        return EngineSignal(self.name, None, 0.40, reason="squeeze but no directional edge")

# ---------------------------------------------------------
# Expert 4: Reversal / Exhaustion (failed breakout / stop run)
# ---------------------------------------------------------
@dataclass
class ReversalExpert:
    name: str = "reversal_exhaustion"
    wick_factor: float = 2.2  # how strong reversal needs to be

    def evaluate(self, c: EngineContext) -> EngineSignal:
        # needs bid/ask (or last extremes) to infer "wick"/rejection
        if c.bid is None or c.ask is None or c.atr is None or c.atr <= 0:
            return EngineSignal(self.name, None, 0.0, reason="missing bid/ask/atr")

        # rejection proxy: spread-adjusted move vs ATR (simple)
        # If price is above donH but snaps back under it -> short reversal
        if c.donH is not None and c.price < c.donH and c.ask > c.donH:
            # stronger if ADX is low-ish (reversal more likely in balance)
            adx = float(c.adx or 0.0)
            conf = 0.55 + clamp((28.0 - adx) / 80.0, 0.0, 0.20)
            return EngineSignal(self.name, "short", clamp(conf, 0.0, 0.80), reason="failed break above high -> reverse short")

        # If price is below donL but snaps back over it -> long reversal
        if c.donL is not None and c.price > c.donL and c.bid < c.donL:
            adx = float(c.adx or 0.0)
            conf = 0.55 + clamp((28.0 - adx) / 80.0, 0.0, 0.20)
            return EngineSignal(self.name, "long", clamp(conf, 0.0, 0.80), reason="failed break below low -> reverse long")

        return EngineSignal(self.name, None, 0.20, reason="no reversal pattern")

# ---------------------------------------------------------
# Expert 5: Risk / Execution / Environment (hard gates)
# ---------------------------------------------------------
@dataclass
class RiskExecutionExpert:
    name: str = "risk_execution"
    max_spread_pips: float = 2.0
    stale_ticks_block: int = 3

    # internal state (per-bot instance)
    _last_tick_time: Optional[str] = None
    _stale_run: int = 0

    def evaluate(self, c: EngineContext) -> EngineSignal:
        # stale feed detection
        if c.time and c.time == self._last_tick_time:
            self._stale_run += 1
        else:
            self._stale_run = 0
            self._last_tick_time = c.time

        if self._stale_run >= self.stale_ticks_block:
            return EngineSignal(self.name, None, 1.0, block=True, reason=f"STALE FEED x{self._stale_run}")

        # spread gate
        if c.spr_pips is not None and float(c.spr_pips) >= self.max_spread_pips:
            return EngineSignal(self.name, None, 1.0, block=True, reason=f"SPREAD HIGH {c.spr_pips:.2f}p")

        return EngineSignal(self.name, None, 0.10, block=False, reason="ok")
