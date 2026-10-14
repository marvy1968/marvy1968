"""FX strategies on mid prices. Each frame() returns long/short entry signals (decided at the bar close), exit_long/exit_short, stop
(distance in price) and rank. Families match the two market regimes:

  TREND  donchian   breakout of the N-bar high/low, exit on the opposite M-bar extreme, stop k x ATR      (daily or H4)
         tsmom      time-series momentum: sign of the L-day return, re-decided every H days, stop k x ATR (daily; the academic one)
  RANGE  range_mr   fade a close outside the Bollinger band when the market is ranging (ADX low, efficiency ratio low),
                    exit at the middle band, stop k x ATR, optional Asian-session-only entries                (H1)
  BOTH   regime     ranging -> range_mr rules, trending -> donchian rules, chosen bar by bar by ADX       (H4)
"""

import numpy as np
import pandas as pd

from alpacabot.indicators import adx, atr, bollinger


def efficiency_ratio(c: pd.Series, n: int = 20) -> pd.Series:
    """Kaufman: |net move| / sum of |bar moves| over n bars. 0 = chop, 1 = straight line."""
    return (c - c.shift(n)).abs() / c.diff().abs().rolling(n).sum()


def regime(m: pd.DataFrame, adx_trend: float = 25.0, adx_range: float = 20.0, er_n: int = 20) -> pd.Series:
    """+1 trending, -1 ranging, 0 unclear. Trending: ADX >= adx_trend and ER >= 0.3; ranging: ADX <= adx_range and ER <= 0.2."""
    a, er = adx(m, 14), efficiency_ratio(m["close"], er_n)
    return pd.Series(np.where((a >= adx_trend) & (er >= 0.3), 1, np.where((a <= adx_range) & (er <= 0.2), -1, 0)), index=m.index)


def _frame(index, long, short, xl, xs, stop, rank=None):
    return pd.DataFrame({"long": long.fillna(False).astype(bool), "short": short.fillna(False).astype(bool),
                         "exit_long": xl.fillna(False).astype(bool), "exit_short": xs.fillna(False).astype(bool),
                         "stop": stop, "rank": rank if rank is not None else pd.Series(0.0, index=index)}, index=index)


class Donchian:
    def __init__(self, entry_n: int = 20, exit_n: int = 10, stop_atr: float = 2.0, max_bars: int = 10 ** 6):
        self.entry_n, self.exit_n, self.stop_atr, self.max_bars = entry_n, exit_n, stop_atr, max_bars
        self.name = f"donchian{entry_n}/{exit_n}"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        hi, lo = m["high"].rolling(self.entry_n).max().shift(1), m["low"].rolling(self.entry_n).min().shift(1)
        xhi, xlo = m["high"].rolling(self.exit_n).max().shift(1), m["low"].rolling(self.exit_n).min().shift(1)
        return _frame(m.index, c > hi, c < lo, c < xlo, c > xhi, self.stop_atr * a, ((c - hi).clip(lower=0) + (lo - c).clip(lower=0)) / a)


class TsMom:
    """Every `hold` bars: long if the `lookback`-bar return is positive, short if negative; held `hold` bars (or stopped)."""

    def __init__(self, lookback: int = 252, hold: int = 21, stop_atr: float = 3.0):
        self.lookback, self.hold, self.stop_atr, self.max_bars = lookback, hold, stop_atr, hold
        self.name = f"tsmom{lookback}/{hold}"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        ret = c / c.shift(self.lookback) - 1
        tick = pd.Series(np.arange(len(m)) % self.hold == 0, index=m.index)
        none = pd.Series(False, index=m.index)
        return _frame(m.index, tick & (ret > 0), tick & (ret < 0), none, none, self.stop_atr * a, ret.abs())


class RangeMR:
    def __init__(self, bb_k: float = 2.0, stop_atr: float = 1.5, asian_only: bool = False, adx_max: float = 20.0, max_bars: int = 24):
        self.bb_k, self.stop_atr, self.asian_only, self.adx_max, self.max_bars = bb_k, stop_atr, asian_only, adx_max, max_bars
        self.name = f"range_mr k{bb_k}{' asia' if asian_only else ''}"

    def frame(self, m):
        c, a = m["close"], atr(m, 14)
        mid_, up, lo = bollinger(c, 20, self.bb_k)
        ranging = (adx(m, 14) <= self.adx_max) & (efficiency_ratio(c, 20) <= 0.3)
        if self.asian_only:
            h = m.index.tz_convert("UTC").hour
            ranging = ranging & pd.Series((h >= 0) & (h < 7), index=m.index)
        return _frame(m.index, ranging & (c < lo), ranging & (c > up), c >= mid_, c <= mid_, self.stop_atr * a,
                      ((lo - c).clip(lower=0) + (c - up).clip(lower=0)) / a)


class RegimeSwitch:
    """Ranging (ADX <= 20) -> Bollinger fade to the middle band; trending (ADX >= 25) -> Donchian breakout. Exits follow the entry's rules."""

    def __init__(self, entry_n: int = 20, exit_n: int = 10, bb_k: float = 2.0, stop_atr: float = 2.0, max_bars: int = 60):
        self.t, self.r = Donchian(entry_n, exit_n, stop_atr), RangeMR(bb_k, stop_atr, False, 20.0, max_bars)
        self.max_bars, self.name = max_bars, f"regime d{entry_n}/{exit_n} k{bb_k}"

    def frame(self, m):
        ft, fr = self.t.frame(m), self.r.frame(m)
        trending = adx(m, 14) >= 25
        long_t, short_t = ft["long"] & trending, ft["short"] & trending
        # an open trade exits by its own family's rule; the simulator holds one trade at a time, so use the union of exits
        # restricted to the family that could have opened it (a trend trade ignores the middle-band exit and vice versa)
        f = _frame(m.index, long_t | fr["long"], short_t | fr["short"], ft["exit_long"], ft["exit_short"], ft["stop"], ft["rank"])
        is_range = (fr["long"] | fr["short"]) & ~(long_t | short_t)
        f.loc[is_range, "stop"] = fr.loc[is_range, "stop"]
        f["family"] = np.where(is_range, "range", "trend")
        f["exit_long"] = ft["exit_long"]
        f["exit_short"] = ft["exit_short"]
        f["exit_long_range"], f["exit_short_range"] = fr["exit_long"], fr["exit_short"]
        return f


class ConsensusMR:
    """The owner's oanda_consensus_daemon.py rules, frozen as found (Oct 2026): enter when 3+ of 5 votes agree and ADX14 < 32.
    Long votes: RSI14 < 32, close <= lower Bollinger(20,2), stoch %K < 25 and %K > %D, z-score(20) < -1.8, CCI(20) < -90 (shorts mirror:
    RSI > 68, close >= upper band, %K > 75 and %K < %D, z > 1.8, CCI > 90). Exit when 2 of 3: RSI back across 50, close back to the middle
    band, %K > 65 (short: < 35). Stop 1.75 x ATR14. The daemon had no time stop; one is declared here (max_bars) so a trade cannot run
    forever in the test."""

    def __init__(self, votes: int = 3, adx_max: float = 32.0, stop_atr: float = 1.75, max_bars: int = 120):
        self.votes, self.adx_max, self.stop_atr, self.max_bars = votes, adx_max, stop_atr, max_bars
        self.name = "consensus_mr (owner's daemon)"

    def frame(self, m):
        from alpacabot.indicators import rsi
        c = m["close"]
        r = rsi(c, 14)
        mid_, up, lo = bollinger(c, 20, 2.0)
        ll, hh = m["low"].rolling(14).min(), m["high"].rolling(14).max()
        k = 100 * (c - ll) / (hh - ll)
        d = k.rolling(3).mean()
        z = (c - c.rolling(20).mean()) / c.rolling(20).std()
        tp = (m["high"] + m["low"] + c) / 3
        cci = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True))
        ok = adx(m, 14) < self.adx_max
        lv = (r < 32).astype(int) + (c <= lo).astype(int) + ((k < 25) & (k > d)).astype(int) + (z < -1.8).astype(int) + (cci < -90).astype(int)
        sv = (r > 68).astype(int) + (c >= up).astype(int) + ((k > 75) & (k < d)).astype(int) + (z > 1.8).astype(int) + (cci > 90).astype(int)
        xl = (r > 50).astype(int) + (c >= mid_).astype(int) + (k > 65).astype(int) >= 2
        xs = (r < 50).astype(int) + (c <= mid_).astype(int) + (k < 35).astype(int) >= 2
        return _frame(m.index, ok & (lv >= self.votes), ok & (sv >= self.votes), xl, xs, self.stop_atr * atr(m, 14), (lv - sv).abs().astype(float))
