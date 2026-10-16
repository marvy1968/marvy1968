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


class RangeBox200:
    """The owner's OANDA range box (drawing, 2026-10-14), for pairs moving sideways: the pair is RANGING when its 200 moving average is flat
    (moved <= flat_atr x ATR14 over 20 bars on at least 80% of the box's bars) and the 20 MA crossed it at least min_crosses times in the
    box, i.e. price keeps swinging up and down around the same level in a repeating pattern. The BOX is the high/low of those `lookback` bars
    (height 2-20 ATR; levels="zscore": the box is SMA200 +- z standard deviations instead of the bar high/low). Buy the bottom (a bar that dips into the bottom 15% of the box and closes back above the box low), sell the top
    (mirror). Sell stop 0.5 ATR outside the box ("ALL" line). Take-profit lines: 25% at 0.25 ATR inside the far edge, 50% at the edge
    (zone_px = the first line); the rest rides with the locked stop. Time stop max_bars."""

    def __init__(self, lookback: int = 100, min_crosses: int = 2, flat_atr: float = 0.5, stop_atr: float = 0.5, max_bars: int = 72,
                 levels: str = "hilo", z: float = 2.0, breakout: str = "off", break_atr: float = 0.1, adx_min: float = 20.0,
                 target: str = "edge", vol_mult: float = 0.0):
        self.lookback, self.min_crosses, self.flat_atr, self.stop_atr, self.max_bars = lookback, min_crosses, flat_atr, stop_atr, max_bars
        self.levels, self.z = levels, z
        self.breakout, self.break_atr, self.adx_min = breakout, break_atr, adx_min
        self.target = target                       # "edge" (drawn: the far side of the box) or "mid" (diagnostics: the box middle)
        self.vol_mult = vol_mult                   # > 0: the signal bar's volume must be >= vol_mult x the prior 20-bar average
        self.name = f"range_box_200_20 (owner's) lb{lookback}" + (f" z{z}" if levels == "zscore" else "") + ("" if breakout == "off" else f" break:{breakout}")

    def frame(self, m):
        from alpacabot.indicators import sma
        c, a = m["close"], atr(m, 14)
        s20, s200 = sma(c, 20), sma(c, 200)
        flat = (s200 - s200.shift(20)).abs() <= self.flat_atr * a                     # the 200 MA is flat ...
        side = np.sign(s20 - s200)
        crosses = (side != side.shift(1)).astype(float).where(side.shift(1).notna(), 0.0).rolling(self.lookback).sum()
        ranging = (flat.astype(float).rolling(self.lookback).mean() >= 0.8) & (crosses >= self.min_crosses)   # ... and the 20 MA keeps
        # crossing it back and forth: price swings up and down around the same level in a repeating pattern
        if self.levels == "zscore":        # owner (2026-10-15): the box = the statistical envelope, SMA200 +- z x the 200-bar standard deviation
            mu, sd = c.shift(1).rolling(200).mean(), c.shift(1).rolling(200).std()
            top, bot = mu + self.z * sd, mu - self.z * sd
        else:                              # the box = the high / low of the last `lookback` bars
            top, bot = m["high"].shift(1).rolling(self.lookback).max(), m["low"].shift(1).rolling(self.lookback).min()
        h = top - bot
        ok = ranging & (h >= 2 * a) & (h <= 20 * a)
        long_ = ok & (m["low"] <= bot + 0.15 * h) & (c > bot) & (c <= bot + 0.33 * h)
        short = ok & (m["high"] >= top - 0.15 * h) & (c < top) & (c >= top - 0.33 * h)
        if self.vol_mult > 0 and "volume" in m:
            vok = m["volume"] >= self.vol_mult * m["volume"].shift(1).rolling(20).mean()
            long_, short = long_ & vok, short & vok
        stop = pd.Series(np.where(long_, c - (bot - self.stop_atr * a), (top + self.stop_atr * a) - c), index=m.index)
        none = pd.Series(False, index=m.index)
        zone_px = np.where(long_, top - 0.25 * a, np.where(short, bot + 0.25 * a, np.nan))
        target = np.where(long_, top - c, np.where(short, c - bot, np.nan))          # backtests: one exit at the far edge
        if self.target == "mid":
            target = np.where(long_, (top + bot) / 2 - c, np.where(short, c - (top + bot) / 2, np.nan))
        if self.breakout != "off":
            # Owner (2026-10-16): virtual orders OUTSIDE the box in case price breaks out and the indicators support it. Virtual = no order is parked
            # at the broker; the bar that CLOSES beyond the edge by break_atr x ATR (first close outside, from a box that was valid the bar before)
            # is bought / sold at the next open. Indicators: ADX14 >= adx_min and the close on the right side of a rising / falling EMA20.
            # Stop 0.5 ATR back inside the box edge; first take-profit line half the box height beyond the edge, the full box height is the target.
            e20 = c.ewm(span=20, adjust=False).mean()
            strong = adx(m, 14) >= self.adx_min
            vb = ok.shift(1).fillna(False).astype(bool)
            up = vb & strong & (c > top + self.break_atr * a) & (c.shift(1) <= top) & (c > e20) & (e20 > e20.shift(3))
            dn = vb & strong & (c < bot - self.break_atr * a) & (c.shift(1) >= bot) & (c < e20) & (e20 < e20.shift(3))
            bstop = pd.Series(np.where(up, c - (top - 0.5 * a), (bot + 0.5 * a) - c), index=m.index)
            if self.breakout == "only":
                long_, short, stop = up, dn, bstop
                zone_px = np.where(up, top + 0.5 * h, np.where(dn, bot - 0.5 * h, np.nan))
                target = np.where(up, top + h - c, np.where(dn, c - (bot - h), np.nan))
            else:                                             # "also": the box fades inside, the breakout orders outside (never both on one bar)
                long_, short = long_ | up, short | dn
                stop = stop.where(~(up | dn), bstop)
                zone_px = np.where(up, top + 0.5 * h, np.where(dn, bot - 0.5 * h, zone_px))
                target = np.where(up, top + h - c, np.where(dn, c - (bot - h), target))
        f = _frame(m.index, long_, short, none, none, stop, h / a)
        f["zone_px"] = zone_px
        f["target"] = target
        return f


class RangeBoxFailed(RangeBox200):
    """REPAIR of the owner's range box (2026-10-16). The box's diagnosis (2016-22, 1,308 trades): price closed beyond the faded edge within a day
    on 68% of trades (avg -0.98R) - the edge usually BREAKS; the 32% where it held averaged +1.2R. So wait for the failed breakout: within the
    last `look` bars a bar CLOSED outside the box (box frozen as it was before that bar, pair ranging), and this bar closes back INSIDE it
    (no deeper than the outer third) - fade it then. Stop 0.25 ATR beyond the false break's extreme; TP1 (zone_px) at the box middle, the
    target at the far edge (target="edge") or the middle (target="mid"). Short = mirror."""

    def __init__(self, lookback: int = 100, look: int = 6, target: str = "edge", stop_buf_atr: float = 0.25, max_bars: int = 72,
                 min_crosses: int = 2, flat_atr: float = 0.5, vol_mult: float = 0.0):
        super().__init__(lookback=lookback, min_crosses=min_crosses, flat_atr=flat_atr, max_bars=max_bars)
        self.look, self.target, self.stop_buf_atr, self.vol_mult = look, target, stop_buf_atr, vol_mult
        self.name = f"range_box failed-breakout lb{lookback} look{look} tgt:{target}"

    def frame(self, m):
        from alpacabot.indicators import sma
        c, a = m["close"], atr(m, 14)
        s20, s200 = sma(c, 20), sma(c, 200)
        flat = (s200 - s200.shift(20)).abs() <= self.flat_atr * a
        side_ = np.sign(s20 - s200)
        crosses = (side_ != side_.shift(1)).astype(float).where(side_.shift(1).notna(), 0.0).rolling(self.lookback).sum()
        ranging = (flat.astype(float).rolling(self.lookback).mean() >= 0.8) & (crosses >= self.min_crosses)
        top, bot = m["high"].shift(1).rolling(self.lookback).max(), m["low"].shift(1).rolling(self.lookback).min()
        h = top - bot
        ok = (ranging & (h >= 2 * a) & (h <= 20 * a)).fillna(False).to_numpy()
        cl, hi, lo = c.to_numpy(float), m["high"].to_numpy(float), m["low"].to_numpy(float)
        T, B, A = top.to_numpy(float), bot.to_numpy(float), a.to_numpy(float)
        n = len(m)
        L_, S_ = np.zeros(n, bool), np.zeros(n, bool)
        stop, z1, tg = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
        # breakout bars: a close outside a valid box (box as it stood before that bar)
        dn = np.flatnonzero(ok & (cl < B))
        up = np.flatnonzero(ok & (cl > T))
        for brk, side in [(k, 1) for k in dn] + [(k, -1) for k in up]:
            t0, b0, hh = T[brk], B[brk], T[brk] - B[brk]
            for i in range(brk + 1, min(n, brk + 1 + self.look)):
                if L_[i] or S_[i]:
                    break
                if side == 1:
                    if cl[i] > b0 and cl[i] <= b0 + hh / 3:              # back inside, in the bottom third
                        ext = lo[brk:i + 1].min()
                        sd = cl[i] - (ext - self.stop_buf_atr * A[i])
                        mid_ = (t0 + b0) / 2
                        if sd > 0 and mid_ > cl[i]:
                            L_[i], stop[i], z1[i] = True, sd, mid_
                            tg[i] = (t0 if self.target == "edge" else mid_) - cl[i]
                        break
                    if cl[i] > b0 + hh / 3:
                        break
                else:
                    if cl[i] < t0 and cl[i] >= t0 - hh / 3:
                        ext = hi[brk:i + 1].max()
                        sd = (ext + self.stop_buf_atr * A[i]) - cl[i]
                        mid_ = (t0 + b0) / 2
                        if sd > 0 and mid_ < cl[i]:
                            S_[i], stop[i], z1[i] = True, sd, mid_
                            tg[i] = cl[i] - (b0 if self.target == "edge" else mid_)
                        break
                    if cl[i] < t0 - hh / 3:
                        break
        if self.vol_mult > 0 and "volume" in m:     # the re-entry bar must come on expanding volume
            vok = (m["volume"] >= self.vol_mult * m["volume"].shift(1).rolling(20).mean()).to_numpy()
            L_, S_ = L_ & vok, S_ & vok
        none = pd.Series(False, index=m.index)
        f = _frame(m.index, pd.Series(L_, index=m.index), pd.Series(S_, index=m.index), none, none, pd.Series(stop, index=m.index), h / a)
        f["zone_px"], f["target"] = z1, tg
        return f


class BoxConfirm:
    """The owner's range box with candlestick + volume + volatility confirmation (alpacabot.confirm, 2026-10-16): every close outside the box
    (100 hourly bars, pair ranging) is read as CONTINUING (strong candle, volume and volatility expanding -> trade the breakout) or FAILING
    (weak candle / no volume, and price closes back inside -> fade it). mode / vol_mult / atr_ratio come from a pre-declared grid."""

    def __init__(self, lookback: int = 100, mode: str = "both", vol_mult: float = 1.5, atr_ratio: float = 1.1, body_min: float = 0.6,
                 look: int = 6, target: str = "mid", max_bars: int = 72, min_crosses: int = 2, flat_atr: float = 0.5, vote: float = 0.0):
        self.lookback, self.mode, self.vol_mult, self.atr_ratio, self.body_min = lookback, mode, vol_mult, atr_ratio, body_min
        self.vote = vote
        self.look, self.target, self.max_bars, self.min_crosses, self.flat_atr = look, target, max_bars, min_crosses, flat_atr
        self.name = f"box_confirm {mode} vol{vol_mult} atr{atr_ratio}"

    def frame(self, m):
        from alpacabot.confirm import confirm_signals
        from alpacabot.indicators import sma
        c, a = m["close"], atr(m, 14)
        al = atr(m, 100)
        s20, s200 = sma(c, 20), sma(c, 200)
        flat = (s200 - s200.shift(20)).abs() <= self.flat_atr * a
        side_ = np.sign(s20 - s200)
        crosses = (side_ != side_.shift(1)).astype(float).where(side_.shift(1).notna(), 0.0).rolling(self.lookback).sum()
        ranging = (flat.astype(float).rolling(self.lookback).mean() >= 0.8) & (crosses >= self.min_crosses)
        top, bot = m["high"].shift(1).rolling(self.lookback).max(), m["low"].shift(1).rolling(self.lookback).min()
        h = top - bot
        ok = (ranging.shift(1).fillna(False).astype(bool) & (h >= 2 * a) & (h <= 20 * a)).fillna(False)   # the box as it stood before the bar
        r = confirm_signals(m["open"], m["high"], m["low"], c, m["volume"] if "volume" in m else None, a, al, top, bot, ok,
                            mode=self.mode, vol_mult=self.vol_mult, atr_ratio=self.atr_ratio, body_min=self.body_min, look=self.look,
                            target=self.target, vote=self.vote)
        none = pd.Series(False, index=m.index)
        f = _frame(m.index, pd.Series(r["long"], index=m.index), pd.Series(r["short"], index=m.index), none, none,
                   pd.Series(r["stop"], index=m.index), h / a)
        f["zone_px"], f["target"], f["kind"] = r["tp1"], r["tgt"], r["kind"]
        sd_ = np.where(r["long"], 1.0, np.where(r["short"], -1.0, np.nan))
        f["zone2_px"] = c.to_numpy(float) + sd_ * r["tgt"]          # TP2 = the full measured move (live runner's second step)
        return f


class HybridBox:
    """The owner's HYBRID zones on forex (2026-10-16, live on practice at reduced size without a backtest, by his decision): the range box of
    RangeBox200 (100 hourly bars, pair ranging), its Fibonacci retracements and the pair's own swing levels inside the box (zonemap.level_map
    on those bars); the entry level is where they agree (zonemap.hybrid_plan). Buy when a bar trades down to the entry level (within 0.25 ATR),
    closes back above it by no more than 0.5 ATR and closes up; stop / TP1 / TP2 from zonemap.hybrid_trade (zone_px = TP1, zone2_px = TP2).
    Short = mirror."""

    def __init__(self, lookback: int = 100, min_crosses: int = 2, flat_atr: float = 0.5, max_bars: int = 72):
        self.lookback, self.min_crosses, self.flat_atr, self.max_bars = lookback, min_crosses, flat_atr, max_bars
        self.name = f"hybrid_box (owner's) lb{lookback}"

    def frame(self, m):
        from alpacabot.indicators import sma
        from alpacabot.zonemap import HYBRID_RULES, hybrid_plan, hybrid_trade, level_map
        c, a = m["close"], atr(m, 14)
        s20, s200 = sma(c, 20), sma(c, 200)
        flat = (s200 - s200.shift(20)).abs() <= self.flat_atr * a
        side_ = np.sign(s20 - s200)
        crosses = (side_ != side_.shift(1)).astype(float).where(side_.shift(1).notna(), 0.0).rolling(self.lookback).sum()
        ranging = (flat.astype(float).rolling(self.lookback).mean() >= 0.8) & (crosses >= self.min_crosses)
        top, bot = m["high"].shift(1).rolling(self.lookback).max(), m["low"].shift(1).rolling(self.lookback).min()
        h = top - bot
        ok = (ranging & (h >= 2 * a) & (h <= 20 * a)).fillna(False).to_numpy()
        o, hi, lo, cl = (m[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        T, B, A, H = top.to_numpy(), bot.to_numpy(), a.to_numpy(), h.to_numpy()
        n = len(m)
        L_, S_ = np.zeros(n, bool), np.zeros(n, bool)
        stop, z1, z2 = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
        r = HYBRID_RULES
        near = ok & ((cl <= B + 0.5 * H + 0.5 * A) | (cl >= T - 0.5 * H - 0.5 * A))       # only bars in the box's outer halves can trigger
        for j in np.flatnonzero(near):
            lv = level_map(m.iloc[max(0, j - self.lookback):j])
            for side in (1, -1):
                plan = hybrid_plan(T[j], B[j], A[j], lv, side)
                if not plan:
                    continue
                Lv, z = plan["entry"], r["zone_atr"] * A[j]
                hit = ((lo[j] <= Lv + z) and (cl[j] >= Lv) and (cl[j] <= Lv + r["chase_atr"] * A[j]) and (cl[j] > o[j])) if side == 1 else \
                      ((hi[j] >= Lv - z) and (cl[j] <= Lv) and (cl[j] >= Lv - r["chase_atr"] * A[j]) and (cl[j] < o[j]))
                if not hit:
                    continue
                tr = hybrid_trade(plan, B[j], T[j], A[j], side, float(cl[j]))
                if not tr:
                    continue
                (L_ if side == 1 else S_)[j] = True
                stop[j], z1[j] = tr["stop_dist"], tr["tp1"]
                z2[j] = tr["tp2"] if tr["tp2"] else np.nan
                break
        none = pd.Series(False, index=m.index)
        f = _frame(m.index, pd.Series(L_, index=m.index), pd.Series(S_, index=m.index), none, none, pd.Series(stop, index=m.index), h / a)
        f["zone_px"], f["zone2_px"] = z1, z2
        f["target"] = np.where(L_, z1 - cl, np.where(S_, cl - z1, np.nan))           # backtests: one exit at TP1
        return f


class BoxGated:
    """A range expert filtered by the owner's OANDA range box (drawing, 2026-10-14; the box test asked for 2026-10-16): the expert's signal counts only
    while the pair is RANGING per the box (200 MA flat on 80% of the last `lookback` bars, the 20 MA crossing it at least twice, box height 2-20 ATR;
    levels = the high/low of those bars). position="valid": that is the only gate; "half": a long also needs the close in the lower half of the box and a
    short in the upper half (fade from the right side of the range); "edge": the outer third. The expert's own exits, stop and target are untouched."""

    def __init__(self, base, lookback: int = 100, position: str = "valid", min_crosses: int = 2, flat_atr: float = 0.5):
        self.base, self.lookback, self.position, self.min_crosses, self.flat_atr = base, lookback, position, min_crosses, flat_atr
        self.max_bars = base.max_bars
        self.name = getattr(base, "name", type(base).__name__) + f" +box:{position}"
        for k in ("lock", "ladder", "zones"):
            if hasattr(base, k):
                setattr(self, k, getattr(base, k))

    def frame(self, m):
        from alpacabot.indicators import sma
        f = self.base.frame(m).copy()
        c, a = m["close"], atr(m, 14)
        s20, s200 = sma(c, 20), sma(c, 200)
        flat = (s200 - s200.shift(20)).abs() <= self.flat_atr * a
        side = np.sign(s20 - s200)
        crosses = (side != side.shift(1)).astype(float).where(side.shift(1).notna(), 0.0).rolling(self.lookback).sum()
        ranging = (flat.astype(float).rolling(self.lookback).mean() >= 0.8) & (crosses >= self.min_crosses)
        top, bot = m["high"].shift(1).rolling(self.lookback).max(), m["low"].shift(1).rolling(self.lookback).min()
        h = top - bot
        ok = (ranging & (h >= 2 * a) & (h <= 20 * a)).fillna(False)
        frac = {"valid": 1.0, "half": 0.5, "edge": 1.0 / 3.0}[self.position]
        pos = (c - bot) / h.where(h > 0)
        long_ok = ok & (pos <= frac) if self.position != "valid" else ok
        short_ok = ok & (pos >= 1 - frac) if self.position != "valid" else ok
        f["long"] = f["long"] & long_ok.fillna(False)
        f["short"] = f["short"] & short_ok.fillna(False)
        return f


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


class DirectionZoneMap:
    """Direction first, then zones, for a forex pair (owner, 2026-10-14) - the same frozen rules as the stock bot, on the pair's own prices.
    Daily bars are built from the strategy's bars (NY 17:00 trading days). Direction = the previous day's bias (up: close > EMA20 > SMA50
    and ADX >= 20; down: mirror; else range). Up -> longs only, down -> shorts only, range -> both sides but only for a mean-reversion
    strategy (style "range"); a trend strategy stands aside in a range. Take-profit: the nearest STRONG zone of the pair's daily zone map
    (swing highs/lows of the last 120 days, merged within 0.5 ATR, 2+ touches) at least min_r x the stop away, measured from the signal
    close; kept only when nearer than the strategy's own target."""

    def __init__(self, base, style: str = "trend", min_r: float = 1.0):
        self.base, self.style, self.min_r = base, style, min_r
        self.max_bars, self.name = base.max_bars, getattr(base, "name", type(base).__name__) + " +direction+zonemap"
        for k in ("zones", "lock"):
            if hasattr(base, k):
                setattr(self, k, getattr(base, k))

    def frame(self, m):
        from alpacabot.zonemap import LEVEL_RULES, bias, level_map, zone_target
        f = self.base.frame(m).copy()
        ny = m.index.tz_convert("America/New_York")
        day = pd.Series((ny - pd.Timedelta(hours=17)).normalize(), index=m.index)
        daily = pd.DataFrame({"open": m["open"].groupby(day).first(), "high": m["high"].groupby(day).max(),
                              "low": m["low"].groupby(day).min(), "close": m["close"].groupby(day).last()})
        b_prev = bias(daily).shift(1).fillna(0).astype(int)              # yesterday's completed daily bias
        days = list(daily.index)
        lv_by_day = {d: level_map(daily.iloc[max(0, k - LEVEL_RULES["lookback"]):k]) if k else [] for k, d in enumerate(days)}
        bar_bias = b_prev.reindex(day.to_numpy()).to_numpy()
        lg, sh = f["long"].to_numpy(bool).copy(), f["short"].to_numpy(bool).copy()
        rng_ok = self.style == "range"
        lg &= (bar_bias == 1) | ((bar_bias == 0) & rng_ok)
        sh &= (bar_bias == -1) | ((bar_bias == 0) & rng_ok)
        f["long"], f["short"] = lg, sh
        tgt = f["target"].to_numpy(float).copy() if "target" in f else np.full(len(f), np.nan)
        c, stop, dvals = m["close"].to_numpy(float), f["stop"].to_numpy(float), day.to_numpy()
        for i in np.flatnonzero(lg | sh):
            side = 1 if lg[i] else -1
            z = zone_target(lv_by_day.get(dvals[i], []), c[i], side, stop[i], self.min_r)
            if z is not None:
                dist = abs(z - c[i])
                tgt[i] = dist if not np.isfinite(tgt[i]) else min(tgt[i], dist)
        f["target"] = tgt
        return f


class Laddered:
    """Any strategy plus the owner's scaled exits (drawing, 2026-10-14): cut steps toward the stop, take-profit steps at the zone with
    stop locks, the rest rides behind a trailing stop. The take-profit zone is the strategy's own drawn line (zone_px) if it has one,
    else the nearer of its target and the 20-bar mean (>= 0.75R, else 1.5R), as in the live runner (without the daily map)."""

    def __init__(self, base, ladder: dict):
        self.base, self.ladder = base, ladder
        self.max_bars, self.name = base.max_bars, getattr(base, "name", type(base).__name__) + " +ladder"

    def frame(self, m):
        return self.base.frame(m)
