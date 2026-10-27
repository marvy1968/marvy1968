"""Classic day-trade, scalp and swing methods from well-known trading books and educators, written with their published (book) rules and
adapted to OANDA forex bars (owner 2026-10-27: "test every expert day trade and scalp method, adapt it to OANDA forex, keep only the proven ones").
Each class has the fxbot frame() protocol (strategies.py): long / short entries decided at a bar close and filled at the next open (bid/ask),
exit flags, stop distance, optional R target. Nothing here is tuned: one setting each, the book's, so the held-out test is honest.
Times are UTC. Forex has no central volume, so volume rules are left out (noted per method).
"""

import numpy as np
import pandas as pd

from alpacabot.indicators import adx, atr, bollinger, ema, rsi, sma
from .strategies import _frame


def _none(m):
    return pd.Series(False, index=m.index)


class SessionORB:
    """Opening-range breakout (Toby Crabel's opening range; the London / New York open breakout taught by Kathy Lien and many FX desks).
    Range = the high / low of `range_start`..`range_end` UTC; trade the first close beyond it until `last_entry` UTC; stop at the other side of the
    range (min 0.5 ATR); target `tp` x the range; out by `flat_hour` UTC (time stop). One trade per day. Meant for M15 bars."""

    def __init__(self, range_start: int = 7, range_end: int = 8, last_entry: int = 11, flat_hour: int = 16, tp: float = 1.0, label: str = "london"):
        self.rs, self.re, self.le, self.fh, self.tp = range_start, range_end, last_entry, flat_hour, tp
        self.max_bars, self.name = 40, f"session_orb_{label}"

    def frame(self, m):
        h = m.index.tz_convert("UTC").hour
        day = pd.Series(m.index.tz_convert("UTC").normalize(), index=m.index)
        inr = pd.Series((h >= self.rs) & (h < self.re), index=m.index)
        hi = m["high"].where(inr).groupby(day).transform("max")
        lo = m["low"].where(inr).groupby(day).transform("min")
        win = pd.Series((h >= self.re) & (h < self.le), index=m.index)
        c, a = m["close"], atr(m, 14)
        L, S = win & (c > hi), win & (c < lo)
        first = (L | S).astype(int).groupby(day).cumsum() == 1
        L, S = L & first, S & first
        rng = (hi - lo).clip(lower=0.5 * a)
        stop = pd.Series(np.where(L, c - lo, hi - c), index=m.index).clip(lower=0.5 * a)
        out = pd.Series(h >= self.fh, index=m.index)
        f = _frame(m.index, L, S, out, out, stop)
        f["target"] = self.tp * rng
        return f


class HolyGrail:
    """Linda Raschke's Holy Grail (Street Smarts, 1995): ADX(14) > 30 and rising trend (+DI > -DI); price pulls back to the 20 EMA; enter when price
    takes out the prior bar's high (longs; shorts mirror). Stop below the pullback low (min 1 ATR); exit at the recent swing high (20-bar high)."""

    def __init__(self, adx_min: float = 30.0, max_bars: int = 20):
        self.adx_min, self.max_bars, self.name = adx_min, max_bars, "raschke_holy_grail"

    def frame(self, m):
        c, h, l = m["close"], m["high"], m["low"]
        a, e, ax = atr(m, 14), ema(c, 20), adx(m, 14)
        up = (h - h.shift(1)).clip(lower=0)
        dn = (l.shift(1) - l).clip(lower=0)
        pdi, mdi = up.where(up > dn, 0).rolling(14).sum(), dn.where(dn > up, 0).rolling(14).sum()
        touched_l = (l.rolling(3).min() <= e)
        touched_s = (h.rolling(3).max() >= e)
        L = (ax > self.adx_min) & (pdi > mdi) & touched_l & (c > h.shift(1))
        S = (ax > self.adx_min) & (mdi > pdi) & touched_s & (c < l.shift(1))
        stop = pd.Series(np.where(L, c - l.rolling(3).min(), h.rolling(3).max() - c), index=m.index).clip(lower=1.0 * a)
        f = _frame(m.index, L, S, c >= h.shift(1).rolling(20).max(), c <= l.shift(1).rolling(20).min(), stop)
        return f


class TurtleSoup:
    """Raschke & Connors, Street Smarts: Turtle Soup. Price makes a new 20-bar low, the previous 20-bar low was set at least 4 bars earlier, and the bar
    closes back above that previous low -> long (fade the failed breakout); shorts mirror. Stop just beyond the new extreme (min 0.5 ATR), target 1.5R,
    time stop 10 bars."""

    def __init__(self, n: int = 20, max_bars: int = 10):
        self.n, self.max_bars, self.name = n, max_bars, "raschke_turtle_soup"

    def frame(self, m):
        c, h, l, a = m["close"], m["high"], m["low"], atr(m, 14)
        prev_lo, prev_hi = l.shift(1).rolling(self.n).min(), h.shift(1).rolling(self.n).max()
        lo_age = l.shift(1).rolling(self.n).apply(lambda x: len(x) - 1 - int(np.argmin(x)), raw=True)
        hi_age = h.shift(1).rolling(self.n).apply(lambda x: len(x) - 1 - int(np.argmax(x)), raw=True)
        L = (l < prev_lo) & (lo_age >= 3) & (c > prev_lo)
        S = (h > prev_hi) & (hi_age >= 3) & (c < prev_hi)
        stop = pd.Series(np.where(L, c - l, h - c), index=m.index).clip(lower=0.5 * a) * 1.05
        f = _frame(m.index, L, S, _none(m), _none(m), stop)
        f["target"] = 1.5 * stop
        return f


class ConnorsRSI2:
    """Larry Connors (Short Term Trading Strategies That Work): close above its 200 SMA and RSI(2) < 10 -> long; exit when the close is above the 5 SMA.
    Shorts mirror (below the 200, RSI(2) > 90, exit under the 5 SMA). Connors uses no stop; a wide 3 ATR stop is kept as a catastrophe stop."""

    def __init__(self, max_bars: int = 20):
        self.max_bars, self.name = max_bars, "connors_rsi2"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        r, s200, s5 = rsi(c, 2), sma(c, 200), sma(c, 5)
        return _frame(m.index, (c > s200) & (r < 10), (c < s200) & (r > 90), c > s5, c < s5, 3.0 * a)


class TTMSqueeze:
    """John Carter's TTM Squeeze (Mastering the Trade): Bollinger(20, 2) inside Keltner(20, 1.5 ATR) for >= 6 bars ("squeeze on"); trade the first bar
    after it releases in the direction of momentum (close minus the average of the 20-bar Donchian middle and the 20 SMA). Stop 1.5 ATR, target 2R,
    time stop 12 bars."""

    def __init__(self, max_bars: int = 12):
        self.max_bars, self.name = max_bars, "carter_ttm_squeeze"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        mid_, up, lo = bollinger(c, 20, 2.0)
        ku, kl = mid_ + 1.5 * a, mid_ - 1.5 * a
        on = (up < ku) & (lo > kl)
        was = on.shift(1).rolling(6).sum() >= 6
        rel = was & ~on
        mom = c - ((m["high"].rolling(20).max() + m["low"].rolling(20).min()) / 2 + sma(c, 20)) / 2
        f = _frame(m.index, rel & (mom > 0), rel & (mom < 0), _none(m), _none(m), 1.5 * a)
        f["target"] = 3.0 * a
        return f


class MacdTrend:
    """The classic MACD + 200 EMA trend entry (widely taught): price above the 200 EMA and the MACD line crosses above its signal line while both are
    below zero -> long; shorts mirror. Stop 1.5 ATR, target 1.5R."""

    def __init__(self, max_bars: int = 30):
        self.max_bars, self.name = max_bars, "macd_200ema"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        line = ema(c, 12) - ema(c, 26)
        sig = ema(line, 9)
        x_up = (line > sig) & (line.shift(1) <= sig.shift(1))
        x_dn = (line < sig) & (line.shift(1) >= sig.shift(1))
        e200 = ema(c, 200)
        f = _frame(m.index, (c > e200) & x_up & (line < 0), (c < e200) & x_dn & (line > 0), _none(m), _none(m), 1.5 * a)
        f["target"] = 2.25 * a
        return f


class EmaScalp:
    """The 8 / 21 EMA crossover scalp with a 50 EMA trend filter (a staple of FX scalping courses): fast EMA crosses the slow one in the direction of
    the 50 EMA -> enter; stop 1 ATR, target 1R, time stop 12 bars. Meant for M5 / M15."""

    def __init__(self, max_bars: int = 12):
        self.max_bars, self.name = max_bars, "ema_8_21_scalp"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        f8, f21, e50 = ema(c, 8), ema(c, 21), ema(c, 50)
        up = (f8 > f21) & (f8.shift(1) <= f21.shift(1)) & (c > e50)
        dn = (f8 < f21) & (f8.shift(1) >= f21.shift(1)) & (c < e50)
        f = _frame(m.index, up, dn, _none(m), _none(m), 1.0 * a)
        f["target"] = 1.0 * a
        return f


class VolatilityBreakout:
    """Larry Williams' volatility breakout (Long-Term Secrets to Short-Term Trading), on daily bars: today's close is above today's open by more than
    `k` x yesterday's range -> buy the next open (shorts mirror); out at the next close (1-bar hold). The book buys intrabar with a stop order; with
    close-decided signals this is the close-confirmed version. Stop 1 ATR."""

    def __init__(self, k: float = 0.6):
        self.k, self.max_bars, self.name = k, 1, "williams_vol_breakout"

    def frame(self, m):
        c, o, a = m["close"], m["open"], atr(m, 14)
        r = (m["high"] - m["low"]).shift(1)
        ok = a.notna()
        return _frame(m.index, ok & (c > o + self.k * r), ok & (c < o - self.k * r), _none(m), _none(m), 1.0 * a)


class PivotBounce:
    """Classic floor-trader pivots (P = (H+L+C)/3 of the previous UTC day; S1 = 2P - H, R1 = 2P - L): a bar dips to S1 and closes back above it ->
    long, target P; shorts mirror at R1. Stop 0.5 ATR beyond the level. Intraday bars (M15 / H1). One trade per side per day."""

    def __init__(self, max_bars: int = 24):
        self.max_bars, self.name = max_bars, "floor_pivot_bounce"

    def frame(self, m):
        idx = m.index.tz_convert("UTC")
        day = pd.Series(idx.normalize(), index=m.index)
        dh, dl, dc = m["high"].groupby(day).max(), m["low"].groupby(day).min(), m["close"].groupby(day).last()
        p = ((dh + dl + dc) / 3).shift(1)
        s1, r1 = (2 * p - dh.shift(1)), (2 * p - dl.shift(1))
        P, S1, R1 = (x.reindex(day).to_numpy() for x in (p, s1, r1))
        c, a = m["close"], atr(m, 14)
        L = pd.Series((m["low"].to_numpy() <= S1) & (c.to_numpy() > S1), index=m.index)
        S = pd.Series((m["high"].to_numpy() >= R1) & (c.to_numpy() < R1), index=m.index)
        L = L & (L.astype(int).groupby(day).cumsum() == 1)
        S = S & (S.astype(int).groupby(day).cumsum() == 1)
        stop = pd.Series(np.where(L, c - S1, R1 - c), index=m.index) + 0.5 * a
        f = _frame(m.index, L, S, _none(m), _none(m), stop.clip(lower=0.3 * a))
        f["target"] = pd.Series(np.where(L, P - c, c - P), index=m.index).clip(lower=0.3 * a)
        return f


class DoubleBollinger:
    """Kathy Lien's Double Bollinger Bands (Day Trading and Swing Trading the Currency Market): a close above the upper 1-sigma band (inside the 2-sigma
    'buy zone') after being below it -> long the trend; exit when the close falls back under the 1-sigma band; shorts mirror. Stop 1.5 ATR."""

    def __init__(self, max_bars: int = 40):
        self.max_bars, self.name = max_bars, "lien_double_bollinger"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        mid_, u1, l1 = bollinger(c, 20, 1.0)
        L = (c > u1) & (c.shift(1) <= u1.shift(1))
        S = (c < l1) & (c.shift(1) >= l1.shift(1))
        return _frame(m.index, L, S, c < u1, c > l1, 1.5 * a)


class InsideBarBreakout:
    """The inside-bar breakout (price-action teaching, e.g. Nial Fuller / Al Brooks' ii patterns): the previous bar was inside the bar before it; this
    bar closes beyond the mother bar's high (long) or low (short). Stop at the other side of the mother bar (min 0.5 ATR), target 2R."""

    def __init__(self, max_bars: int = 20):
        self.max_bars, self.name = max_bars, "inside_bar_breakout"

    def frame(self, m):
        h, l, c, a = m["high"], m["low"], m["close"], atr(m, 14)
        inside = (h.shift(1) <= h.shift(2)) & (l.shift(1) >= l.shift(2))
        L, S = inside & (c > h.shift(2)), inside & (c < l.shift(2))
        stop = pd.Series(np.where(L, c - l.shift(2), h.shift(2) - c), index=m.index).clip(lower=0.5 * a)
        f = _frame(m.index, L, S, _none(m), _none(m), stop)
        f["target"] = 2.0 * stop
        return f


# method -> (constructor, granularities to test). Day-trade / scalp methods on M15 (the EMA scalp also on M5), swing methods on H1 / H4 / D.
METHODS = {"session_orb_london": (lambda: SessionORB(7, 8, 11, 16, 1.0, "london"), ("M15",)),
           "session_orb_newyork": (lambda: SessionORB(12, 13, 16, 20, 1.0, "newyork"), ("M15",)),
           "raschke_holy_grail": (HolyGrail, ("M15", "H1", "H4")),
           "raschke_turtle_soup": (TurtleSoup, ("M15", "H1", "H4", "D")),
           "connors_rsi2": (ConnorsRSI2, ("H1", "H4", "D")),
           "carter_ttm_squeeze": (TTMSqueeze, ("M15", "H1", "H4")),
           "macd_200ema": (MacdTrend, ("M15", "H1", "H4")),
           "ema_8_21_scalp": (EmaScalp, ("M5", "M15")),
           "williams_vol_breakout": (VolatilityBreakout, ("D",)),
           "floor_pivot_bounce": (PivotBounce, ("M15", "H1")),
           "lien_double_bollinger": (DoubleBollinger, ("H1", "H4", "D")),
           "inside_bar_breakout": (InsideBarBreakout, ("H1", "H4", "D"))}
