"""Shared order/position types. Strategies are pure functions of completed daily bars, so the
backtest and the live runner call exactly the same code."""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Order:
    side: int                       # +1 long, -1 short
    kind: str = "stop"              # "stop" (resting stop entry), "limit" (resting limit entry) or "market"
    price: Optional[float] = None   # stop-entry level (absolute)
    from_open: Optional[float] = None  # stop-entry level = today's open + from_open (Crabel)
    arm: Optional[float] = None     # order only becomes live after price trades through this level
    sl: Optional[float] = None      # absolute protective stop
    sl_from_open: Optional[float] = None  # stop = today's open + sl_from_open (Crabel opposite side)
    sl_dist: Optional[float] = None # stop distance from fill
    sl_daylow: bool = False         # stop = 1 tick beyond today's extreme at fill (Turtle Soup)
    tp: Optional[float] = None
    tp_dist: Optional[float] = None
    close_eod: bool = False         # day trade: flat at the 17:00 NY close
    expires_days: int = 0           # 0 = good for today only (OANDA GFD / GTD next 17:00 NY)
    gtd_minutes: Optional[int] = None  # intraday signals: order expires this many minutes after placement
    meta: dict = field(default_factory=dict)
    armed: bool = False

@dataclass
class Position:
    side: int
    entry: float
    sl: float
    tp: Optional[float]
    risk: float
    entry_di: int
    entry_time: object
    close_eod: bool = False
    meta: dict = field(default_factory=dict)

@dataclass
class Manage:
    close: bool = False
    new_sl: Optional[float] = None
    reason: str = ""

class Strategy:
    sid = "base"; expert = ""; book = ""; style = ""
    warmup = 30
    intraday = False   # True: signal() is called every run with recent M5 bars (intraday systems)
    max_per_day = 1    # intraday: max signals per pair per trading day
    pairs = None       # optional system-specific pair list (overrides config "pairs")
    def signal(self, hist, bars, pair, state, now):
        """Intraday hook. hist: completed daily bars before today; bars: completed M5 bars of the
        last few trading days (UTC 'time', open/high/low/close/volume, 'td' = trading date);
        now: aware datetime. Return Orders to place now."""
        return []
    def plan(self, hist, pair, state):  # hist: completed daily bars before today
        return []
    def manage(self, pos, hist, pair, state):  # hist includes the day just closed
        return Manage()
    def after_stop(self, pos, day_k, pair, state):
        return None
