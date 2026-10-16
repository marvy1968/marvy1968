"""Strategies. A signal is decided at the CLOSE of bar `i` using bars <= i only; the engine fills it at the OPEN of bar i+1.

Intraday (1-minute bars, one symbol-day at a time):
  VwapReversion      scalp: price stretched far below session VWAP (z-score) and the bar closes up -> long back to VWAP
  OpeningRangeBreak  day trade: close above the opening-range high (after the range forms) with price above VWAP
Swing (daily bars, whole history of one symbol):
  RsiPullback        long-term uptrend + RSI(2) washed out -> buy, sell on the first bounce (mean reversion)
  Breakout           close above the N-day high in an uptrend, exit on a trailing M-day low (trend following)

Long only by default: shorting needs a margin account and borrow, and adds costs this backtest does not model.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import adx, atr, bollinger, ema, macd_hist, psar_below_price, rsi, session_vwap, sma, stoch_fast


@dataclass
class Signal:
    i: int                 # decision bar (fill at open of i+1)
    side: int = 1          # +1 long, -1 short
    stop_dist: float = 0.0  # price distance from entry to the stop
    target_r: float = 0.0   # take-profit in multiples of stop_dist (0 = none)
    max_bars: int = 0       # time stop in bars after entry (0 = hold to the end of the day)
    tag: str = ""
    managed: bool = False   # use the virtual exit manager (exits.ExitState) instead of a fixed stop/target
    atr: float = 0.0        # ATR at the decision bar (managed exits)
    adx: float = 0.0        # ADX at the decision bar (regime-scaled target)
    trail: float = 1.5      # ATR multiple for the initial stop and the trailing stop
    tp_scale: float = 1.0   # research knob on the regime-scaled take-profit
    exit_col: str = ""      # optional boolean column of the bar frame: when true at a bar's close, sell at the next open
    target_px: float = 0.0  # optional absolute take-profit price (the range box's far edge); with zones on it replaces the zone-map target


@dataclass
class VwapReversion:
    z_entry: float = 2.0       # enter when (close - vwap) / rolling std <= -z_entry
    z_window: int = 30
    stop_atr: float = 1.0      # stop distance in 1-minute ATR(14) multiples
    target: str = "vwap"       # "vwap" (back to VWAP) or "r" (fixed multiple)
    target_r: float = 1.5
    max_bars: int = 20
    start: str = "10:00"
    end: str = "15:30"
    max_trades: int = 3
    name: str = "vwap_reversion"

    def signals(self, df: pd.DataFrame) -> list[Signal]:
        vwap = session_vwap(df)
        dev = df["close"] - vwap
        z = dev / dev.rolling(self.z_window, min_periods=self.z_window).std()
        a = atr(df, 14)
        up = df["close"] > df["open"]
        t = df.index.strftime("%H:%M")
        ok = (z <= -self.z_entry) & up & (t >= self.start) & (t <= self.end) & a.notna()
        out = []
        for i in np.flatnonzero(ok.to_numpy())[: self.max_trades * 4]:
            sd = float(self.stop_atr * a.iloc[i])
            tr = self.target_r if self.target == "r" else max(float(vwap.iloc[i] - df["close"].iloc[i]) / max(sd, 1e-9), 0.25)
            out.append(Signal(int(i), 1, sd, tr, self.max_bars, "vwap"))
        return out

    def placebo(self, df: pd.DataFrame, n: int, rng: np.random.Generator) -> list[Signal]:
        a = atr(df, 14)
        t = df.index.strftime("%H:%M")
        idx = np.flatnonzero(((t >= self.start) & (t <= self.end) & a.notna()).to_numpy())
        if len(idx) == 0:
            return []
        pick = rng.choice(idx, size=min(n, len(idx)), replace=False)
        return [Signal(int(i), 1, float(self.stop_atr * a.iloc[i]), self.target_r if self.target == "r" else 1.0, self.max_bars, "placebo")
                for i in sorted(pick)]


@dataclass
class OpeningRangeBreak:
    range_minutes: int = 15
    stop: str = "range_low"    # "range_low" or an ATR multiple
    stop_atr: float = 1.0
    target_r: float = 2.0
    end: str = "11:30"         # no new entries after this time
    require_vwap: bool = True
    max_bars: int = 0          # time stop in minutes after entry (0 = hold to the close)
    name: str = "opening_range_break"

    def signals(self, df: pd.DataFrame) -> list[Signal]:
        if len(df) < self.range_minutes + 5:
            return []
        t = df.index.strftime("%H:%M")
        first = df.index[0].normalize() + pd.Timedelta(hours=9, minutes=30)
        in_range = df.index < first + pd.Timedelta(minutes=self.range_minutes)
        if in_range.sum() < self.range_minutes // 2:
            return []
        hi, lo = df["high"][in_range].max(), df["low"][in_range].min()
        vwap = session_vwap(df)
        a = atr(df, 14)
        after = (~in_range) & (t <= self.end) & a.notna()
        ok = after & (df["close"] > hi)
        if self.require_vwap:
            ok &= df["close"] > vwap
        for i in np.flatnonzero(ok.to_numpy())[:1]:                       # first breakout of the day only
            entry = float(df["close"].iloc[i])
            sd = entry - lo if self.stop == "range_low" else self.stop_atr * float(a.iloc[i])
            if sd <= 0:
                return []
            return [Signal(int(i), 1, float(sd), self.target_r, self.max_bars, "orb")]
        return []

    def placebo(self, df: pd.DataFrame, n: int, rng: np.random.Generator) -> list[Signal]:
        t = df.index.strftime("%H:%M")
        a = atr(df, 14)
        idx = np.flatnonzero(((t >= "09:45") & (t <= self.end) & a.notna()).to_numpy())
        if len(idx) == 0 or n == 0:
            return []
        i = int(rng.choice(idx))
        lo = float(df["low"].iloc[: self.range_minutes].min())
        sd = float(df["close"].iloc[i] - lo) if self.stop == "range_low" else float(self.stop_atr * a.iloc[i])
        return [Signal(i, 1, max(sd, 0.01), self.target_r, self.max_bars, "placebo")] if sd > 0 else []


@dataclass
class RsiPullback:
    trend_sma: int = 200
    rsi_n: int = 2
    rsi_entry: float = 10.0
    exit_sma: int = 5
    rsi_exit: float = 70.0
    max_days: int = 7
    stop_atr: float = 3.0
    name: str = "rsi_pullback"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        f = pd.DataFrame(index=d.index)
        f["r"] = rsi(d["close"], self.rsi_n)
        f["trend"] = d["close"] > sma(d["close"], self.trend_sma)
        f["exit_ma"] = sma(d["close"], self.exit_sma)
        f["atr"] = atr(d, 14)
        f["entry"] = f["trend"] & (f["r"] < self.rsi_entry) & f["atr"].notna()
        f["exit"] = (d["close"] > f["exit_ma"]) | (f["r"] > self.rsi_exit)
        return f

    def stop_dist(self, f, i):
        return float(self.stop_atr * f["atr"].iloc[i])


@dataclass
class Breakout:
    lookback: int = 55
    exit_lookback: int = 20
    trend_sma: int = 200
    max_days: int = 60
    stop_atr: float = 3.0
    name: str = "breakout"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        f = pd.DataFrame(index=d.index)
        f["hh"] = d["high"].rolling(self.lookback, min_periods=self.lookback).max().shift(1)
        f["ll"] = d["low"].rolling(self.exit_lookback, min_periods=self.exit_lookback).min().shift(1)
        f["trend"] = d["close"] > sma(d["close"], self.trend_sma)
        f["atr"] = atr(d, 14)
        f["entry"] = f["trend"] & (d["close"] > f["hh"]) & f["atr"].notna()
        f["exit"] = d["close"] < f["ll"]
        return f

    def stop_dist(self, f, i):
        return float(self.stop_atr * f["atr"].iloc[i])


@dataclass
class DualRegime:
    """Dual-Regime Consensus Engine: an ADX router sends each bar to a trend panel or a reversion panel; a long needs 2 of 3 votes.

    TREND  (ADX >= adx_split): EMA 9 > EMA 21 | MACD histogram > 0 and rising | Parabolic SAR below price
    RANGE  (ADX <  adx_split): RSI(7) < 30 and RSI(14) < 40 | low touches the lower Bollinger band(20,2) | fast stochastic (8,3) < 20
    A signal fires on the bar where the panel first reaches 2 of 3 (it was below 2 on the previous bar), so it does not re-enter every bar.
    Exits are the virtual manager's (break-even lock, ATR trail, ADX-scaled target); see exits.py.
    Works on any bar size; research uses 5-minute bars. Long only.
    """
    adx_split: float = 25.0
    trail: float = 1.5
    tp_scale: float = 1.0
    max_bars: int = 0
    start: str = "09:45"
    end: str = "15:30"
    max_trades: int = 3
    needs: int = 2
    name: str = "dual_regime"

    @staticmethod
    def features(df: pd.DataFrame) -> pd.DataFrame:
        """Causal indicator columns on the continuous bar series (overnight included, so the indicators are warm at the open)."""
        f = pd.DataFrame(index=df.index)
        f["f_adx"], f["f_atr"] = adx(df, 14), atr(df, 14)
        f["f_ema_ok"] = ema(df["close"], 9) > ema(df["close"], 21)
        h = macd_hist(df["close"])
        f["f_macd_ok"] = (h > 0) & (h > h.shift(1))
        f["f_psar_ok"] = psar_below_price(df)
        f["f_rsi_ok"] = (rsi(df["close"], 7) < 30) & (rsi(df["close"], 14) < 40)
        lo, _, _ = bollinger(df["close"])
        f["f_bb_ok"] = df["low"] <= lo
        f["f_stoch_ok"] = stoch_fast(df, 8, 3) < 20
        return f

    def _votes(self, d: pd.DataFrame):
        trend = d["f_adx"] >= self.adx_split
        tv = d["f_ema_ok"].astype(int) + d["f_macd_ok"].astype(int) + d["f_psar_ok"].astype(int)
        rv = d["f_rsi_ok"].astype(int) + d["f_bb_ok"].astype(int) + d["f_stoch_ok"].astype(int)
        votes = tv.where(trend, rv)
        return trend, votes

    def signals(self, df: pd.DataFrame) -> list[Signal]:
        if "f_adx" not in df:
            raise ValueError("DualRegime needs the feature columns: build the frames with split_days(..., feature_fn=DualRegime.features)")
        trend, votes = self._votes(df)
        fresh = (votes >= self.needs) & ~((votes.shift(1) >= self.needs) & (trend.shift(1) == trend))
        t = df.index.strftime("%H:%M")
        ok = fresh & (t >= self.start) & (t <= self.end) & df["f_atr"].notna() & df["f_adx"].notna()
        out = []
        for i in np.flatnonzero(ok.to_numpy())[: self.max_trades]:
            a = float(df["f_atr"].iloc[i])
            out.append(Signal(int(i), 1, self.trail * a, 0.0, self.max_bars, "regime_trend" if trend.iloc[i] else "regime_range",
                              True, a, float(df["f_adx"].iloc[i]), self.trail, self.tp_scale))
        return out

    def placebo(self, df: pd.DataFrame, n: int, rng: np.random.Generator) -> list[Signal]:
        t = df.index.strftime("%H:%M")
        idx = np.flatnonzero(((t >= self.start) & (t <= self.end) & df["f_atr"].notna() & df["f_adx"].notna()).to_numpy())
        if len(idx) == 0 or n == 0:
            return []
        out = []
        for i in sorted(rng.choice(idx, size=min(n, len(idx)), replace=False)):
            a = float(df["f_atr"].iloc[i])
            out.append(Signal(int(i), 1, self.trail * a, 0.0, self.max_bars, "placebo", True, a, float(df["f_adx"].iloc[i]), self.trail, self.tp_scale))
        return out


class SectorTopDown:
    """The 'Sector Top-Down / Multi-Factor' rules exactly as supplied (daily bars, long only), so they can be tested outside the data they were found on.
    ENTRY (completed daily bar, filled at the NEXT open): close > EMA20 > SMA50 and the stock's 20-day return beats SPY's 20-day return by >= 3 points.
    STOP: signal-bar EMA20 - 1 x ATR14 (a fixed price; a gap through it fills at the open). EXIT: a daily close below EMA20, filled at the next open.
    Competing signals on one day are ranked by relative strength (the supplied rules do not say; this is an assumption).
    bench: SPY daily closes (tz-aware index, same convention as the stock data)."""

    def __init__(self, bench: pd.Series, rs_margin: float = 0.03, ema_n: int = 20, sma_n: int = 50, atr_mult: float = 1.0):
        self.bench, self.rs_margin, self.ema_n, self.sma_n, self.atr_mult = bench, rs_margin, ema_n, sma_n, atr_mult
        self.max_days = 10 ** 6
        self.name = "sector_top_down"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        c = d["close"]
        e, sm, a = ema(c, self.ema_n), sma(c, self.sma_n), atr(d, 14)
        spy20 = self.bench.pct_change(20).reindex(d.index, method="ffill")
        rs = c.pct_change(20) - spy20
        f = pd.DataFrame(index=d.index)
        f["entry"] = ((c > e) & (e > sm) & (rs >= self.rs_margin) & a.notna() & spy20.notna()).fillna(False)
        f["exit"] = (c < e).fillna(False)
        f["stop"] = e - self.atr_mult * a
        f["rs"] = rs
        return f

    def stop_dist(self, f, i):                      # only used as a validity check; the real stop is stop_price()
        return float("inf") if pd.isna(f["stop"].iloc[i]) else 1.0

    def stop_price(self, f, i):
        return float(f["stop"].iloc[i])

    def rank(self, f, i):
        return float(f["rs"].iloc[i])


class SectorTopDownShort:
    """The owner's 'follow the trend down' (2026-10-15): the live Sector Top-Down rules mirrored for SHORTS (daily bars).
    ENTRY (completed daily bar, sold short at the NEXT open): close < EMA20 < SMA50 and the stock's 20-day return trails SPY's by >= 3 points.
    STOP: signal-bar EMA20 + 1 x ATR14 (a fixed price; a gap through it fills at the open). EXIT: a daily close above EMA20, covered at the next open.
    Optional pre-declared gates: stage4 (Weinstein: close below a falling 150-day average), market_weak (SPY below its own 50-day average)."""

    def __init__(self, bench: pd.Series, rs_margin: float = 0.03, ema_n: int = 20, sma_n: int = 50, atr_mult: float = 1.0,
                 stage4: bool = False, market_weak: bool = False):
        self.bench, self.rs_margin, self.ema_n, self.sma_n, self.atr_mult = bench, rs_margin, ema_n, sma_n, atr_mult
        self.stage4, self.market_weak = stage4, market_weak
        self.max_days = 10 ** 6
        self.name = "sector_top_down_short" + ("+stage4" if stage4 else "") + ("+weak_market" if market_weak else "")

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        c = d["close"]
        e, sm, a = ema(c, self.ema_n), sma(c, self.sma_n), atr(d, 14)
        spy20 = self.bench.pct_change(20).reindex(d.index, method="ffill")
        rs = c.pct_change(20) - spy20
        entry = (c < e) & (e < sm) & (rs <= -self.rs_margin) & a.notna() & spy20.notna()
        if self.stage4:
            ma = c.rolling(150).mean()
            entry = entry & (c < ma) & (ma < ma.shift(20))
        if self.market_weak:
            b = self.bench.reindex(d.index, method="ffill")
            entry = entry & (b < b.rolling(50).mean())
        f = pd.DataFrame(index=d.index)
        f["entry"] = entry.fillna(False)
        f["exit"] = (c > e).fillna(False)
        f["stop"] = e + self.atr_mult * a
        f["rs"] = -rs                                   # the weakest stock first
        return f


class GatedSectorTopDown:
    """SectorTopDown with an extra entry gate (bool Series by date, True = entries allowed); exits and stops are unchanged."""

    def __init__(self, base: "SectorTopDown", gate: pd.Series | None):
        self.base, self.gate = base, gate
        self.max_days, self.name = base.max_days, base.name + "+gate"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        f = self.base.frame(d)
        if self.gate is not None:
            f["entry"] = f["entry"] & self.gate.reindex(d.index).fillna(True).astype(bool)
        return f

    def stop_dist(self, f, i):
        return self.base.stop_dist(f, i)

    def stop_price(self, f, i):
        return self.base.stop_price(f, i)

    def rank(self, f, i):
        return self.base.rank(f, i)



class BookA:
    """'Book A' exactly as the owner froze it (Sept 2026), daily bars, long only: ENTRY a close above the prior 55-day high (Donchian
    breakout), filled at the next open; EXIT a close below the prior 20-day low, filled at the next open; initial STOP 1.5 x ATR(14)
    below the fill (no trailing). Competing signals ranked by how far the close cleared the 55-day high, in ATRs (an assumption).
    Portfolio rules (0.5% risk, max 3 positions) live in the test's Portfolio."""

    def __init__(self, entry_n: int = 55, exit_n: int = 20, stop_atr: float = 1.5):
        self.entry_n, self.exit_n, self.stop_atr = entry_n, exit_n, stop_atr
        self.max_days, self.name = 10 ** 6, "book_a"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        c, a = d["close"], atr(d, 14)
        hi, lo = d["high"].rolling(self.entry_n).max().shift(1), d["low"].rolling(self.exit_n).min().shift(1)
        f = pd.DataFrame(index=d.index)
        f["entry"] = ((c > hi) & a.notna()).fillna(False)
        f["exit"] = (c < lo).fillna(False)
        f["atr"] = a
        f["rank"] = (c - hi) / a
        return f

    def stop_dist(self, f, i):
        return float(self.stop_atr * f["atr"].iloc[i]) if pd.notna(f["atr"].iloc[i]) else float("nan")

    def rank(self, f, i):
        v = f["rank"].iloc[i]
        return float(v) if pd.notna(v) else 0.0


class Elite20:
    """'Elite-20' daily mean reversion as the owner froze it, long only. z = (close - 20-day mean) / 20-day std. ENTRY when z reclaims
    -2.5 (yesterday z <= -2.5, today z > -2.5), filled at the next open; STOP fixed at the signal bar's mean - 3.5 std; TARGET a close at
    or above the 50-day SMA, sold at the next open. Not in the frozen rules, so declared here: a 60-day time stop and ranking by how deep
    yesterday's z was. Max 3 positions and one per sector live in the test's Portfolio / group_of."""

    def __init__(self, n: int = 20, entry_z: float = -2.5, stop_z: float = -3.5, target_n: int = 50, max_days: int = 60):
        self.n, self.entry_z, self.stop_z, self.target_n = n, entry_z, stop_z, target_n
        self.max_days, self.name = max_days, "elite20"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        c = d["close"]
        m, sd = c.rolling(self.n).mean(), c.rolling(self.n).std()
        z = (c - m) / sd
        f = pd.DataFrame(index=d.index)
        f["entry"] = ((z.shift(1) <= self.entry_z) & (z > self.entry_z)).fillna(False)
        f["exit"] = (c >= sma(c, self.target_n)).fillna(False)
        f["stop"] = m + self.stop_z * sd
        f["rank"] = -z.shift(1)
        return f

    def stop_dist(self, f, i):
        return 1.0 if pd.notna(f["stop"].iloc[i]) else float("nan")

    def stop_price(self, f, i):
        return float(f["stop"].iloc[i])

    def rank(self, f, i):
        v = f["rank"].iloc[i]
        return float(v) if pd.notna(v) else 0.0


class RandomEntries:
    """Placebo: the wrapped strategy's exits and stops, but its entries moved to random days (same count, only days where a stop exists)."""

    def __init__(self, base, seed: int):
        self.base, self.seed = base, seed
        self.max_days, self.name = base.max_days, base.name + "_placebo"

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        f = self.base.frame(d)
        ok = [i for i in range(len(f)) if np.isfinite(self.base.stop_dist(f, i))]
        k = min(int(f["entry"].sum()), len(ok))
        pick = np.random.default_rng(self.seed).choice(ok, size=k, replace=False) if k else []
        e = np.zeros(len(f), bool)
        e[list(pick)] = True
        f["entry"] = e
        return f

    def stop_dist(self, f, i):
        return self.base.stop_dist(f, i)

    def rank(self, f, i):
        return self.base.rank(f, i)

    def __getattr__(self, name):                     # stop_price only when the wrapped strategy has one
        if name == "stop_price" and hasattr(self.base, "stop_price"):
            return self.base.stop_price
        raise AttributeError(name)



class Zoned:
    """Any daily swing strategy plus the owner's trade zones (engine.ZONES): skip fills > 0.5 ATR past the signal close, take profit at the
    prior 20-day high if >= 1R away (else 2R), exit at the next open when a +1.5R trade closes under the previous day's low."""

    def __init__(self, base, zones: dict | None = None):
        from .engine import ZONES
        self.base, self.zones = base, dict(zones or ZONES)
        self.max_days, self.name = base.max_days, getattr(base, "name", type(base).__name__) + "+zones"

    def frame(self, d):
        return self.base.frame(d)

    def stop_dist(self, f, i):
        return self.base.stop_dist(f, i)

    def rank(self, f, i):
        return self.base.rank(f, i) if hasattr(self.base, "rank") else 0.0

    def __getattr__(self, name):
        if name == "stop_price" and hasattr(self.base, "stop_price"):
            return self.base.stop_price
        raise AttributeError(name)
