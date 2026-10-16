"""Ten rule-based experts on 5-minute bars, plus the shared causal feature table they read.

Shorts: every expert's SELL-SHORT signal is its buy signal on the mirrored price series (open -> -open, high -> -low, low -> -high,
close -> -close). All indicators here are symmetric under that flip, so a short is literally the long rule turned upside down.
features_both() adds the mirrored features as m_* columns; short_view() turns a day frame into the mirrored frame the experts read.

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
  opening_range_breakout (candidate, off live)  the first close above the first 30 minutes' high (10:00-11:30), above session VWAP; stop
                   at the range midpoint (Crabel / Fisher "ACD" / Zarattini-style opening-range breakout; rules frozen before any test)

Every expert fires at the close of bar i (fills at the open of i+1) and ships its own stop, target / exit rule, and a time stop.
"""

import numpy as np
import pandas as pd

from .indicators import adx, atr, bollinger, ema, rsi, session_vwap, sma
from .strategies import DualRegime, Signal

TREND_EXPERTS = {"velez_elephant", "raschke_grail", "brooks_pullback", "donchian_turtle"}
RANGE_EXPERTS = {"connors_rsi2", "bollinger_bounce", "williams_r", "vwap_dev", "velez_180", "range_box", "fib_box", "hybrid_box"}   # range_box = the owner's box strategy (brain.BoxSignals)
# Candidate expert (owner's idea, 2026-10-14: "trading the open"). Study verdict ADD (2026-10-15, one-year provisional: PF 0.66 vs 0.65 on train,
# 0.82 vs 0.78 on validate with the ten, max drawdown 29.9% vs 35.8%), so it is the 11th live expert. Alone it still loses on validation (PF 0.84).
CANDIDATE_EXPERTS = ["opening_range_breakout"]
TEN_EXPERTS = ["velez_elephant", "velez_180", "raschke_grail", "brooks_pullback", "connors_rsi2", "bollinger_bounce", "williams_r",
               "donchian_turtle", "vwap_dev", "dual_regime"]
ALL_EXPERTS = TEN_EXPERTS + CANDIDATE_EXPERTS


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
    # multi-timeframe trend DIRECTION (+1 / -1 / 0) and STRENGTH (ADX) on the last COMPLETED 1-hour and 15-minute bars (owner, 2026-10-15):
    # direction = EMA9 vs EMA21 with the close on the same side of EMA21; strength = Wilder ADX(14) of that timeframe
    for tag, rule in (("h1", "60min"), ("m15", "15min")):
        r = df.resample(rule, label="left", closed="left", origin="start_day").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        e9, e21, cr = ema(r["close"], 9), ema(r["close"], 21), r["close"]
        d = pd.Series(np.where((e9 > e21) & (cr > e21), 1, np.where((e9 < e21) & (cr < e21), -1, 0)), index=r.index).shift(1)
        f[f"f_{tag}_dir"] = d.reindex(df.index, method="ffill").fillna(0).astype(int)
        f[f"f_{tag}_adx"] = adx(r, 14).shift(1).reindex(df.index, method="ffill")
    # indicator exits ("sell when they say sell"): decided at a close, filled at the next open
    f["x_connors"] = (c > f["f_sma5"]) | (f["f_rsi2"] > 70)
    f["x_boll"] = c >= f["f_bb_mid"]
    f["x_willr"] = f["f_willr"] > -20
    f["x_vwap"] = c >= vw
    return f


def mirror(df: pd.DataFrame) -> pd.DataFrame:
    """The price series upside down: a short on df is a long on mirror(df)."""
    return pd.DataFrame({"open": -df["open"], "high": -df["low"], "low": -df["high"], "close": -df["close"], "volume": df["volume"]}, index=df.index)


def features_both(df: pd.DataFrame) -> pd.DataFrame:
    """Long features (f_*, x_*) plus the same features on the mirrored series (m_f_*, m_x_*) for the short side."""
    return features_all(df).join(features_all(mirror(df)).add_prefix("m_"))


def short_view(d: pd.DataFrame) -> pd.DataFrame:
    """A day frame as the short side sees it: mirrored prices with the m_* features under their plain names (gate columns kept)."""
    v = mirror(d)
    for col in d.columns:
        if col.startswith("m_"):
            v[col[2:]] = d[col]
        elif col.startswith("g_"):
            v[col] = d[col]
    return v


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
        out["williams_r"] = (d["f_willr"].shift(1) < -80) & (d["f_willr"] >= -80) & (c > d["f_ema20"] - 0.005 * d["f_ema20"].abs())
        out["donchian_turtle"] = (c > d["f_hh20"]) & (d["f_rvol"] >= 1.2) & (d["f_adx"] >= 20)
        out["vwap_dev"] = (d["f_vwap_z"] <= -2.0) & (c > o)
        out["opening_range_breakout"] = self._orb(d)
        return out

    @staticmethod
    def _orb(d: pd.DataFrame) -> pd.Series:
        """First close above the opening range (09:30-09:59 bars of the same day) between 10:00 and 11:30, range 1-6 ATR wide, above VWAP.
        On the mirrored (short) frame the same code is the break of the opening-range LOW."""
        t = d.index.strftime("%H:%M")
        day = pd.Series(d.index.date, index=d.index)
        in_or = pd.Series(t < "10:00", index=d.index)
        orh = d["high"].where(in_or).groupby(day).transform("max")
        orl = d["low"].where(in_or).groupby(day).transform("min")
        width, c = orh - orl, d["close"]
        first = (c > orh) & (c.shift(1) <= orh)
        win = pd.Series((t >= "10:00") & (t <= "11:30"), index=d.index)
        ok = (width >= d["f_atr"]) & (width <= 6 * d["f_atr"]) & (c > d["f_vwap"])
        out = (first & win & ok).fillna(False)
        out.attrs["orb_mid"] = ((orh + orl) / 2)
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
                elif name == "opening_range_breakout":
                    sd = cl - float(masks[name].attrs["orb_mid"].iloc[i])
                    if 0 < sd <= 2.5 * A:
                        found.append((name, _sig(i, sd, 2.0, 48, name)))
                elif name == "donchian_turtle":
                    found.append((name, Signal(int(i), 1, 1.5 * A, 0.0, 0, name, True, A, float(d["f_adx"].iloc[i]), 1.5, 1.0)))
        if names is None or "dual_regime" in names:
            for s in DualRegime(start=self.window[0], end=self.window[1], max_trades=50).signals(d):
                found.append(("dual_regime", Signal(s.i, 1, s.stop_dist, 0.0, 0, "dual_regime", True, s.atr, s.adx, s.trail, s.tp_scale)))
        return sorted(found, key=lambda x: x[1].i)


SWING_EXITS = {"connors_rsi2": "x_connors", "bollinger_bounce": "x_boll", "williams_r": "x_willr"}
SWING_EXPERTS = ["velez_elephant", "velez_180", "raschke_grail", "brooks_pullback", "connors_rsi2", "bollinger_bounce", "williams_r",
                 "donchian_turtle"]     # vwap_dev (session VWAP) and dual_regime have no daily-bar meaning


class ExpertSwing:
    """One expert on DAILY bars, held for days (engine.simulate_swing): decide at the close, fill at the next open, stop stop_atr x ATR below,
    exit on the expert's own exit rule (if it has one) or after max_days. Long only. Costs per trade are tiny next to multi-day moves,
    which is the point of this test."""

    def __init__(self, name: str, max_days: int = 10, stop_atr: float = 2.0, placebo_seed: int | None = None):
        self.name, self.max_days, self.stop_atr, self.placebo_seed = name, max_days, stop_atr, placebo_seed

    def frame(self, d: pd.DataFrame) -> pd.DataFrame:
        dd = d.join(features_all(d))
        m = Experts().masks(dd)[self.name].fillna(False) & dd["f_sma200"].notna() & dd["f_atr"].notna()
        xcol = SWING_EXITS.get(self.name)
        ex = dd[xcol].fillna(False).astype(bool) if xcol else pd.Series(False, index=dd.index)
        entry = m.astype(bool)
        if self.placebo_seed is not None:                    # same number of entries, on random days: does the entry rule beat a drifting market?
            ok = np.flatnonzero((dd["f_sma200"].notna() & dd["f_atr"].notna()).to_numpy())
            pick = np.random.default_rng(self.placebo_seed).choice(ok, size=min(int(entry.sum()), len(ok)), replace=False) if len(ok) else []
            entry = pd.Series(False, index=dd.index)
            entry.iloc[pick] = True
        return pd.DataFrame({"entry": entry, "exit": ex, "atr": dd["f_atr"]}, index=dd.index)

    def stop_dist(self, f, i):
        return float(self.stop_atr * f["atr"].iloc[i])
