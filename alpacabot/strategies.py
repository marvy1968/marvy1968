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

from .indicators import atr, ema, rsi, session_vwap, sma


@dataclass
class Signal:
    i: int                 # decision bar (fill at open of i+1)
    side: int = 1          # +1 long, -1 short
    stop_dist: float = 0.0  # price distance from entry to the stop
    target_r: float = 0.0   # take-profit in multiples of stop_dist (0 = none)
    max_bars: int = 0       # time stop in bars after entry (0 = hold to the end of the day)
    tag: str = ""


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
            return [Signal(int(i), 1, float(sd), self.target_r, 0, "orb")]
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
        return [Signal(i, 1, max(sd, 0.01), self.target_r, 0, "placebo")] if sd > 0 else []


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
