"""Shared types and position-management rules (identical in live and backtest)."""
from dataclasses import dataclass
import pandas as pd
import config


@dataclass
class Signal:
    expert: str
    symbol: str
    side: int          # +1 long, -1 short
    price: float       # reference price (signal bar close)
    stop: float
    target: float
    risk: float        # |price - stop|
    reason: str
    trigger: float = 0.0   # >0: stop-entry order at this price (fills only if traded through)


@dataclass
class Position:
    symbol: str
    expert: str
    sector: str
    side: int
    qty: float
    entry: float
    stop: float
    target: float
    risk: float        # initial risk per share (1R)
    opened_at: str     # ISO timestamp
    regime: str = ""
    reason: str = ""
    be_moved: bool = False
    confirmed: bool = True
    order_id: str = ""
    target_live: bool = True   # False when only a plain stop protects the position (bot watches target)
    peak: float = 0.0          # runner mode: best price reached since entry
    trailing: bool = False     # runner mode: stop has been trailed above the initial -1R


def allowed_dirs(style, regime):
    """Regime -> which directions an expert may trade.
    trend     : BULL long only, BEAR short only, SIDEWAYS off
    reversion : BULL buy dips, BEAR sell rips, SIDEWAYS both
    breakout  : BULL long, BEAR short, SIDEWAYS both
    """
    if regime == "BULL":
        return {1}
    if regime == "BEAR":
        return {-1}
    return {1, -1} if style in ("reversion", "breakout") else set()


def confirm_dirs(style, a, i):
    """Directions confirmed by the multi-timeframe votes (see config.DIRECTION_CONFIRM)."""
    votes = [a["dir60"][i], a["dir15"][i]]
    if style != "reversion":
        votes.append(a["dir5"][i])
    if all(v > 0 for v in votes):
        return {1}
    if all(v < 0 for v in votes):
        return {-1}
    return set()


def vol_filter_ok(a, i):
    """v4: volatility contracting AND volume rising at bar i (NaN -> not ok)."""
    ar, vr = a["vx_atr_ratio"][i], a["vx_vol_ratio"][i]
    return bool(ar < config.VX_ATR_RATIO and vr > config.VX_VOL_RATIO)


def zone_ok(side, a, i):
    """365-day zone filter: longs only inside the buy zone, shorts only inside the sell zone (NaN -> no)."""
    lo, hi = (a["zbuy_lo"][i], a["zbuy_hi"][i]) if side > 0 else (a["zsell_lo"][i], a["zsell_hi"][i])
    return bool(lo <= a["close"][i] <= hi)


def session_limits(close_mod=390):
    """[FIX] Entry cut-off and flatten time relative to the ACTUAL close (half days close at 13:00)."""
    return (min(config.LAST_ENTRY_MOD, close_mod - 30), min(config.FLATTEN_MOD, close_mod - 5))


def runner_trail(pos, a, i):
    """v6 profit squeeze. Uses bar i's high/low (known at its close); the new stop applies from bar i+1."""
    ext = float(a["high"][i] if pos.side > 0 else a["low"][i])
    if pos.peak == 0.0 or pos.side * (ext - pos.peak) > 0:
        pos.peak = ext
    if pos.risk <= 0:
        return None
    peak_r = pos.side * (pos.peak - pos.entry) / pos.risk
    if peak_r < config.TRAIL_START_R:
        return None
    give = max(config.TRAIL_GIVEBACK_MIN,
               config.TRAIL_GIVEBACK_START - config.TRAIL_GIVEBACK_STEP * (peak_r - config.TRAIL_START_R))
    new = round(pos.entry + pos.side * (peak_r - give) * pos.risk, 2)
    if pos.side * (new - pos.stop) >= 0.01:        # ratchet only: never loosen
        return ("trail", new)
    return None


def check_management(pos, expert, a, i, flatten_mod=None):
    """Evaluated at the close of bar i. Returns ("exit", reason) | ("breakeven", new_stop) | None."""
    if a["mod"][i] >= (config.FLATTEN_MOD if flatten_mod is None else flatten_mod):
        return ("exit", "eod_flatten")
    if config.RUNNER_MODE:
        return runner_trail(pos, a, i)
    held = (a["ts"][i] - pd.Timestamp(pos.opened_at)).total_seconds() / 60 + 1
    if held >= expert.max_hold:
        return ("exit", "time_stop")
    reason = expert.exit_signal(a, i, pos)
    if reason:
        return ("exit", reason)
    if not pos.be_moved and pos.risk > 0:
        r = pos.side * (a["close"][i] - pos.entry) / pos.risk
        if r >= config.BREAKEVEN_TRIGGER_R:
            return ("breakeven", round(pos.entry + pos.side * 0.05 * pos.risk, 2))
    return None
