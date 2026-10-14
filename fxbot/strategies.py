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


class AsianBreakout:
    """Asian-range breakout at the London open (H1). Range = high/low of bars 00:00-06:59 UTC; only on days the range is narrower than
    1.5x its 20-day median. Entry on a bar closing 07:00-10:00 UTC: long when close > Asian high + buffer x ATR14, short when close <
    Asian low - buffer x ATR14; first signal of the day only. Stop = distance to the far side of the range, capped at 1.5 ATR. Exit at the
    close of the bar starting `exit_hour` UTC (max_bars 9 as a backstop)."""

    def __init__(self, buffer_atr: float = 0.1, exit_hour: int = 16, max_bars: int = 9):
        self.buffer_atr, self.exit_hour, self.max_bars = buffer_atr, exit_hour, max_bars
        self.name = f"asian_breakout b{buffer_atr} x{exit_hour}"

    def frame(self, m):
        idx = m.index.tz_convert("UTC")
        hour, day = idx.hour, idx.normalize()
        a = atr(m, 14)
        asia = pd.Series(hour < 7, index=m.index)
        hi = m["high"].where(asia).groupby(day).transform("max")
        lo = m["low"].where(asia).groupby(day).transform("min")
        width = hi - lo
        daily_w = width.groupby(day).first()
        ok_w = (daily_w < 1.5 * daily_w.rolling(20).median()).reindex(day).to_numpy()
        window = pd.Series((hour >= 6) & (hour <= 9), index=m.index)       # H1 bars starting 06..09 close 07:00..10:00
        c = m["close"]
        long_ = window & ok_w & (c > hi + self.buffer_atr * a)
        short = window & ok_w & (c < lo - self.buffer_atr * a)
        sig = (long_ | short)
        firstmask = sig & (sig.astype(int).groupby(day).cumsum() == 1)
        stop_l, stop_s = (c - lo).clip(upper=1.5 * a), (hi - c).clip(upper=1.5 * a)
        stop = pd.Series(np.where(long_, stop_l, stop_s), index=m.index)
        out_ = pd.Series(hour == self.exit_hour, index=m.index)
        return _frame(m.index, long_ & firstmask, short & firstmask, out_, out_, stop, (c - hi).abs() / a)


class RangeBoxFade:
    """The owner's RangeBox_Master FOREX rules on H1 (frozen; partial take-profits replaced by one exit at the middle of the box).
    Gate: ADX14 < 20, |EMA50 - EMA50[2 bars ago]| <= 0.25 x ATR14, >= 47 of the last 50 closes inside Bollinger(20,2). Box = last 40
    bars' high/low, width >= 2 ATR. Long: close <= box low + 0.15 x width and close <= lower band; short mirrors. Exit at the box middle;
    stop 1.2 ATR; max 40 bars. Note: the 47-of-50 gate is very strict (pure noise rarely passes it), so expect few trades."""

    def __init__(self, stop_atr: float = 1.2, max_bars: int = 40):
        self.stop_atr, self.max_bars, self.name = stop_atr, max_bars, "rangebox_fade (owner's)"

    def frame(self, m):
        from alpacabot.indicators import ema
        c, a = m["close"], atr(m, 14)
        _, up, lo = bollinger(c, 20, 2.0)
        inside = ((c <= up) & (c >= lo)).astype(int).rolling(50).sum() >= 47
        e = ema(c, 50)
        gate = (adx(m, 14) < 20) & ((e - e.shift(2)).abs() <= 0.25 * a) & inside
        bh, bl = m["high"].rolling(40).max(), m["low"].rolling(40).min()
        w = bh - bl
        gate = gate & (w >= 2 * a)
        mid_box = bl + 0.5 * w
        return _frame(m.index, gate & (c <= bl + 0.15 * w) & (c <= lo), gate & (c >= bh - 0.15 * w) & (c >= up),
                      c >= mid_box, c <= mid_box, self.stop_atr * a, (w / a))


class EmaPullback:
    """Trend pullback to EMA20 (from the owner's Velez 20-EMA / Weinstein bots). Long: close > EMA20 > SMA(trend_n), EMA20 rising over 5
    bars, the bar's low touched EMA20 and it closed back above it as an up bar; short mirrors. Stop = distance to EMA20 - 0.5 x bar range,
    at least 0.5 ATR. Exit when the close falls 0.5 ATR through EMA20; max 30 bars."""

    def __init__(self, trend_n: int = 100, max_bars: int = 30):
        self.trend_n, self.max_bars, self.name = trend_n, max_bars, f"ema20_pullback sma{trend_n}"

    def frame(self, m):
        from alpacabot.indicators import ema, sma
        c, a, o = m["close"], atr(m, 14), m["open"]
        e, s = ema(c, 20), sma(c, self.trend_n)
        rng = m["high"] - m["low"]
        long_ = (c > e) & (e > s) & (e > e.shift(5)) & (m["low"] <= e) & (c > o)
        short = (c < e) & (e < s) & (e < e.shift(5)) & (m["high"] >= e) & (c < o)
        stop_l = (c - (e - 0.5 * rng)).clip(lower=0.5 * a)
        stop_s = ((e + 0.5 * rng) - c).clip(lower=0.5 * a)
        stop = pd.Series(np.where(long_, stop_l, stop_s), index=m.index)
        return _frame(m.index, long_, short, c < e - 0.5 * a, c > e + 0.5 * a, stop, ((c - e).abs() / a))


class NR7Breakout:
    """Crabel NR7 (daily): yesterday's range was the narrowest of 7; today closes beyond it -> enter that way. Stop = distance to the NR7
    bar's far side; exit on a close back inside the NR7 bar or after 5 bars."""

    def __init__(self, max_bars: int = 5):
        self.max_bars, self.name = max_bars, "nr7_breakout"

    def frame(self, m):
        r = m["high"] - m["low"]
        nr7 = (r == r.rolling(7).min()).shift(1).fillna(False).astype(bool)
        ph, pl, c = m["high"].shift(1), m["low"].shift(1), m["close"]
        long_, short = nr7 & (c > ph), nr7 & (c < pl)
        stop = pd.Series(np.where(long_, c - pl, ph - c), index=m.index)
        hold_h, hold_l = ph.where(long_ | short).ffill(), pl.where(long_ | short).ffill()
        return _frame(m.index, long_, short, c < hold_h, c > hold_l, stop, (r.shift(1) / atr(m, 14)))


class LienFader:
    """Kathy Lien's 'Fader' (Day Trading and Swing Trading the Currency Market), as frozen in the owner's fx-experts bot: daily ADX14 < 35;
    long after price first breaks the previous NY day's low by 15 pips and then trades 15 pips above the previous day's high (short mirrors);
    stop 30 pips, target 60 pips; entry orders good for the day. Run on H1 bars: the trigger is a bar CLOSE beyond the level, filled at the
    next bar's open (a stop order would fill at the level itself, so this is a conservative stand-in). One entry per side per day."""

    def __init__(self, break_pips: float = 15, stop_pips: float = 30, target_pips: float = 60, adx_max: float = 35, max_bars: int = 72):
        self.break_pips, self.stop_pips, self.target_pips, self.adx_max, self.max_bars = break_pips, stop_pips, target_pips, adx_max, max_bars
        self.name = "lien_fader (owner's fx-experts)"

    def frame(self, m):
        pip = 0.01 if float(m["close"].iloc[-1]) > 20 else 0.0001             # JPY pairs quote around 100+
        ny = m.index.tz_convert("America/New_York")
        day = pd.Series((ny - pd.Timedelta(hours=17)).normalize(), index=m.index)  # trading day starts 17:00 NY
        dh, dl = m["high"].groupby(day).max(), m["low"].groupby(day).min()
        dc = m["close"].groupby(day).last()
        daily = pd.DataFrame({"open": m["open"].groupby(day).first(), "high": dh, "low": dl, "close": dc})
        dadx = adx(daily, 14).shift(1)                                         # yesterday's completed daily ADX
        pdh, pdl = dh.shift(1).reindex(day).to_numpy(), dl.shift(1).reindex(day).to_numpy()
        ok = (dadx < self.adx_max).reindex(day).to_numpy()
        lo_today = m["low"].groupby(day).cummin().to_numpy()
        hi_today = m["high"].groupby(day).cummax().to_numpy()
        c, b = m["close"].to_numpy(), self.break_pips * pip
        long_ = ok & (lo_today <= pdl - b) & (c >= pdh + b)
        short = ok & (hi_today >= pdh + b) & (c <= pdl - b)
        L, S = pd.Series(long_, index=m.index), pd.Series(short, index=m.index)
        L = L & (L.astype(int).groupby(day).cumsum() == 1)
        S = S & (S.astype(int).groupby(day).cumsum() == 1)
        none = pd.Series(False, index=m.index)
        f = _frame(m.index, L, S, none, none, pd.Series(self.stop_pips * pip, index=m.index))
        f["target"] = self.target_pips * pip
        return f


class MaCrossBigBuild:
    """The owner's 'Big Build' OANDA system as recalled (no code survives): enter on a fresh SMA20/SMA60 cross, with an RSI14 filter
    (no buy when RSI >= 70, no sell when RSI <= 30); fixed 80-pip stop and 160-pip target. Frozen exactly; the reported PF 1.59-1.77 came
    from tuning on the same data, so this is the out-of-sample check."""

    def __init__(self, fast: int = 20, slow: int = 60, stop_pips: float = 80, target_pips: float = 160, max_bars: int = 10 ** 6):
        self.fast, self.slow, self.stop_pips, self.target_pips, self.max_bars = fast, slow, stop_pips, target_pips, max_bars
        self.name = f"ma{fast}/{slow} sl{stop_pips} tp{target_pips} (Big Build)"

    def frame(self, m):
        from alpacabot.indicators import rsi, sma
        pip = 0.01 if float(m["close"].iloc[-1]) > 20 else 0.0001
        c = m["close"]
        f_, s_ = sma(c, self.fast), sma(c, self.slow)
        up = (f_ > s_) & (f_.shift(1) <= s_.shift(1))
        dn = (f_ < s_) & (f_.shift(1) >= s_.shift(1))
        r = rsi(c, 14)
        fr = _frame(m.index, up & (r < 70), dn & (r > 30), dn, up, pd.Series(self.stop_pips * pip, index=m.index))
        fr["target"] = self.target_pips * pip
        return fr


class F0Reversion:
    """The owner's F0 (H4, frozen as recorded): z = (close - SMA100) / std100. Long when z reclaims -2.5 (previous bar <= -2.5, this bar
    > -2.5), stop at the signal bar's SMA100 - 3.5 std, exit when the close returns to SMA100; short mirrors. Not in the record, declared
    here: a 120-bar (20 trading days) time stop. Recorded result: 628 events, +0.101R, PF 1.16 (no filters; adding filters made it worse)."""

    def __init__(self, n: int = 100, entry_z: float = 2.5, stop_z: float = 3.5, max_bars: int = 120):
        self.n, self.entry_z, self.stop_z, self.max_bars = n, entry_z, stop_z, max_bars
        self.name = f"f0_reversion sma{n} {entry_z}/{stop_z} (owner's)"

    def frame(self, m):
        c = m["close"]
        mu, sd = c.rolling(self.n).mean(), c.rolling(self.n).std()
        z = (c - mu) / sd
        long_ = (z.shift(1) <= -self.entry_z) & (z > -self.entry_z)
        short = (z.shift(1) >= self.entry_z) & (z < self.entry_z)
        stop = pd.Series(np.where(long_, c - (mu - self.stop_z * sd), (mu + self.stop_z * sd) - c), index=m.index)
        return _frame(m.index, long_, short, c >= mu, c <= mu, stop, z.shift(1).abs())



class Locked:
    """Any strategy plus the profit lock: stop to break-even once the trade is +be_at R, and after +trail_after R the stop trails
    trail_r R behind the best price. Updated at each bar's end, active from the next bar (no intrabar look-ahead)."""

    def __init__(self, base, be_at: float = 1.0, trail_after: float = 2.0, trail_r: float = 1.0):
        self.base, self.lock = base, (be_at, trail_after, trail_r)
        self.max_bars, self.name = base.max_bars, getattr(base, "name", type(base).__name__) + " +lock"

    def frame(self, m):
        return self.base.frame(m)



ZONES = {"chase_atr": 0.5, "lookback": 20, "min_r": 1.0, "default_r": 2.0, "exhaust_r": 1.5}
# Per-strategy zone profiles by STYLE (same as the stock bot): trend/breakout -> prior 20-bar high/low (>= 1R) else 2R;
# mean-reversion -> back at the 20-bar average (>= 0.75R) else 1.5R. Levels always come from each pair's own bars.
ZONE_PROFILES = {"trend": dict(ZONES, target="extreme"),
                 "range": {"chase_atr": 0.5, "lookback": 20, "min_r": 0.75, "default_r": 1.5, "exhaust_r": 1.5, "target": "mean"}}


class Zoned:
    """Any strategy plus trade zones (owner's idea, rules frozen 2026-10-14): ENTRY zone - skip a trade whose fill is more than 0.5 ATR past
    the signal close (no chasing); INVALIDATION zone - the strategy's own stop; EXHAUSTION zone - take profit at the prior 20-bar high (long)
    / low (short) when it is at least 1R away, else at 2R (whichever of this and the strategy's own target is nearer), and exit at the next
    open when a trade that has been +1.5R closes beyond the previous bar's low (long) / high (short)."""

    def __init__(self, base, profile: str = "trend", **kw):
        self.base, self.zones = base, {**ZONE_PROFILES[profile], **kw}
        self.max_bars, self.name = base.max_bars, getattr(base, "name", type(base).__name__) + " +zones"
        if hasattr(base, "lock"):
            self.lock = base.lock

    def frame(self, m):
        f = self.base.frame(m).copy()
        n = self.zones["lookback"]
        f["zone_hi"], f["zone_lo"] = m["high"].rolling(n).max(), m["low"].rolling(n).min()
        f["zone_close"], f["zone_atr"] = m["close"], atr(m, 14)
        f["zone_mean"] = m["close"].rolling(n).mean()
        return f
