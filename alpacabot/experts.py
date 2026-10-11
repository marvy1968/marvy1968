"""Ten rule-based experts on 5-minute bars (long only), plus the shared causal feature table they read.

  velez_elephant   Elephant Bar: a big-bodied bar (>= 1.5 ATR, closes in the top of its range) breaking the prior high, with the trend
  velez_180        180 reversal: a bullish bar that fully reverses the prior bearish bar, in an uptrend
  raschke_grail    Holy Grail: ADX > 30, rising 20 EMA, pullback that touches it and turns up
  brooks_pullback  with-trend pullback to the 20 EMA ending in a bull reversal bar (the "second entry")
  connors_rsi2     RSI(2) < 10 above the 200-bar average; exits when RSI(2) > 70 or price > its 5-bar average
  bollinger_bounce close back inside the lower Bollinger band while RSI(14) < 40; exits at the middle band
  williams_r       Williams %R turning up through -80; exits above -20
  donchian_turtle  close above the prior 20-bar high with relative volume >= 1.2; managed exits
  vwap_dev         price 2 sigma below session VWAP and the bar closes up; exits back at VWAP
  dual_regime      the ADX-routed 2-of-3 consensus engine (strategies.DualRegime) with the virtual exit manager

Every expert fires at the close of bar i (fills at the open of i+1) and ships its own stop, target / exit rule, and a time stop.
"""

import numpy as np
import pandas as pd

from .indicators import adx, atr, bollinger, ema, rsi, session_vwap, sma
from .strategies import DualRegime, Signal

TREND_EXPERTS = {"velez_elephant", "raschke_grail", "brooks_pullback", "donchian_turtle"}
RANGE_EXPERTS = {"connors_rsi2", "bollinger_bounce", "williams_r", "vwap_dev", "velez_180"}
ALL_EXPERTS = ["velez_elephant", "velez_180", "raschke_grail", "brooks_pullback", "connors_rsi2", "bollinger_bounce", "williams_r",
               "donchian_turtle", "vwap_dev", "dual_regime"]


def features_all(df: pd.DataFrame) -> pd.DataFrame:
    """Causal feature table for every expert and the brain's filters (computed once on the continuous bar series)."""
    f = DualRegime.features(df)
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    f["f_atr50"] = atr(df, 50)
    f["f_ema20"], f["f_ema50"] = ema(c, 20), ema(c, 50)
    f["f_sma200"], f["f_sma5"] = sma(c, 200), sma(c, 5)
    f["f_rsi2"], f["f_rsi14"] = rsi(c, 2), rsi(c, 14)
    lo, mid, up = bollinger(c)
    f["f_bb_lo"], f["f_bb_mid"] = lo, mid
    hh, ll = h.rolling(14, min_periods=14).max(), l.rolling(14, min_periods=14).min()
    f["f_willr"] = -100 * (hh - c) / (hh - ll).replace(0, np.nan)
    f["f_hh20"] = h.rolling(20, min_periods=20).max().shift(1)
    f["f_rvol"] = v / v.rolling(20, min_periods=20).mean().shift(1)
    vw = session_vwap(df)
    dev = c - vw
    f["f_vwap"], f["f_vwap_z"] = vw, dev / dev.rolling(12, min_periods=12).std()
    f["f_atr_ratio"] = f["f_atr"] / f["f_atr50"]
    # higher-timeframe trend: last COMPLETED 15-minute bar's close above its rising EMA(21)
    r15 = df.resample("15min", label="left", closed="left", origin="start_day").agg({"close": "last"}).dropna()
    e = ema(r15["close"], 21)
    up15 = ((r15["close"] > e) & (e > e.shift(1))).shift(1)
    f["f_mtf_up"] = up15.reindex(df.index, method="ffill").fillna(False).astype(bool)
    # indicator exits ("sell when they say sell"): decided at a close, filled at the next open
    f["x_connors"] = (c > f["f_sma5"]) | (f["f_rsi2"] > 70)
    f["x_boll"] = c >= f["f_bb_mid"]
    f["x_willr"] = f["f_willr"] > -20
    f["x_vwap"] = c >= vw
    return f


def _sig(i, stop_dist, target_r=0.0, max_bars=0, tag="", exit_col="") -> Signal:
    return Signal(int(i), 1, float(stop_dist), target_r, max_bars, tag, exit_col=exit_col)


class Experts:
    """All experts as boolean 'buy now' masks over a day frame (needs features_all columns)."""

    def __init__(self, window=("09:50", "15:30")):
        self.window = window

    def masks(self, d: pd.DataFrame) -> dict[str, pd.Series]:
        c, o, h, l = d["close"], d["open"], d["high"], d["low"]
        rng = (h - l).replace(0, np.nan)
        a = d["f_atr"]
        body = (c - o).abs()
        out = {}
        out["velez_elephant"] = (rng >= 1.5 * a) & (body >= 0.7 * rng) & ((h - c) <= 0.2 * rng) & (c > o) & (c > h.shift(1)) & (c > d["f_ema20"])
        out["velez_180"] = (o.shift(1) > c.shift(1)) & (c > o) & (c > o.shift(1)) & (c > d["f_sma200"]) & (rng >= 0.8 * a)
        out["raschke_grail"] = (d["f_adx"] > 30) & (d["f_ema20"] > d["f_ema20"].shift(5)) & (l <= d["f_ema20"]) & (c > d["f_ema20"]) & (c > c.shift(1))
        pulled = (l.shift(1) < l.shift(3)) & (l.shift(2) < l.shift(4))
        out["brooks_pullback"] = (d["f_ema20"] > d["f_ema50"]) & pulled & (c > o) & ((c - l) >= 0.66 * rng) & ((l - d["f_ema20"]).abs() <= 0.5 * a)
        out["connors_rsi2"] = (d["f_rsi2"] < 10) & (c > d["f_sma200"])
        out["bollinger_bounce"] = (c.shift(1) < d["f_bb_lo"].shift(1)) & (c > d["f_bb_lo"]) & (d["f_rsi14"] < 40)
        out["williams_r"] = (d["f_willr"].shift(1) < -80) & (d["f_willr"] >= -80) & (c > d["f_ema20"] * 0.995)
        out["donchian_turtle"] = (c > d["f_hh20"]) & (d["f_rvol"] >= 1.2) & (d["f_adx"] >= 20)
        out["vwap_dev"] = (d["f_vwap_z"] <= -2.0) & (c > o)
        return out

    def signals(self, d: pd.DataFrame, names=None) -> list[tuple[str, Signal]]:
        t = d.index.strftime("%H:%M")
        gate = (t >= self.window[0]) & (t <= self.window[1]) & d["f_atr"].notna().to_numpy()
        a = d["f_atr"]
        sma200_ok = d["f_sma200"].notna()
        found = []
        masks = self.masks(d)
        for name in (names or ALL_EXPERTS):
            if name == "dual_regime":
                continue
            m = (masks[name].fillna(False) & gate & sma200_ok).to_numpy()
            for i in np.flatnonzero(m):
                A = float(a.iloc[i])
                lo, cl = float(d["low"].iloc[i]), float(d["close"].iloc[i])
                if name == "velez_elephant":
                    sd = cl - lo
                    if 0 < sd <= 2.5 * A:
                        found.append((name, _sig(i, sd, 2.0, 18, name)))
                elif name == "velez_180":
                    sd = cl - min(lo, float(d["low"].iloc[i - 1]))
                    if 0 < sd <= 2.5 * A:
                        found.append((name, _sig(i, sd, 2.0, 18, name)))
                elif name == "raschke_grail":
                    sd = max(cl - lo, 0.5 * A)
                    found.append((name, _sig(i, sd, 2.0, 24, name)))
                elif name == "brooks_pullback":
                    sd = max(cl - lo, 0.5 * A)
                    found.append((name, _sig(i, sd, 2.0, 24, name)))
                elif name == "connors_rsi2":
                    found.append((name, _sig(i, 2.0 * A, 0.0, 24, name, "x_connors")))
                elif name == "bollinger_bounce":
                    found.append((name, _sig(i, 1.5 * A, 0.0, 24, name, "x_boll")))
                elif name == "williams_r":
                    found.append((name, _sig(i, 1.5 * A, 0.0, 24, name, "x_willr")))
                elif name == "vwap_dev":
                    found.append((name, _sig(i, 1.5 * A, 0.0, 12, name, "x_vwap")))
                elif name == "donchian_turtle":
                    found.append((name, Signal(int(i), 1, 1.5 * A, 0.0, 0, name, True, A, float(d["f_adx"].iloc[i]), 1.5, 1.0)))
        if names is None or "dual_regime" in names:
            for s in DualRegime(start=self.window[0], end=self.window[1], max_trades=50).signals(d):
                found.append(("dual_regime", Signal(s.i, 1, s.stop_dist, 0.0, 0, "dual_regime", True, s.atr, s.adx, s.trail, s.tp_scale)))
        return sorted(found, key=lambda x: x[1].i)
