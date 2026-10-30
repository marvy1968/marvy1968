"""Already-trending stocks: entry from the scanner, the work is the TP zone and the sell (owner 2026-10-30: "instead of picking where to buy,
use the scanner to find an already trending stock, then predict where the TP zone should be and the sell"; "2 week test").

PRE-DECLARED. Universe: the 55 stocks; "trending" = fxbot.strongtrend long_state == 1 at the prior close. Daily bars, 10 bps a side.
  A PULLBACK  a trending stock whose day trades down to its 20-day EMA (as of the prior close) from above: buy at the EMA (an open below it
              fills at the open). Stop 1 ATR below the fill; TP zone = fill + 1.5 ATR; sell signal = a close under the 50-day average;
              10 sessions max. The fill day counts only the stop and the close (daily bars cannot order a same-day TP).
  B HOLD      a stock that turned trending at the prior close (first day of long_state 1 after 5+ days without): buy the open; stop 1.5 ATR;
              TP zone = fill + 2 ATR; sell signal = a close under the 20-day EMA; 10 sessions max.
One position per stock at a time. Control: the same number of trades on random stocks of the 55 on the same days, same exits (20 seeds).
Read: the last 2 weeks (owner) and the last 6 months for context. PASS (2 weeks): net R > 0 and beats 18 of 20 random runs; small samples
are flagged.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

COST = 10.0 / 1e4


def _dates(d):
    d = d.copy()
    idx = pd.DatetimeIndex(d.index)
    d.index = pd.to_datetime((idx.tz_convert("America/New_York") if idx.tz is not None else idx).date)
    return d[~d.index.duplicated(keep="last")].sort_index().astype(float)


def prep(d: pd.DataFrame) -> pd.DataFrame:
    from fxbot.strongtrend import votes
    o, h, l, c = (d[k] for k in ("open", "high", "low", "close"))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame({"o": o, "h": h, "l": l, "c": c}, index=d.index)
    f["atr"] = tr.rolling(14).mean().shift(1)                 # as of the prior close
    f["ema20"] = c.ewm(span=20, adjust=False).mean()
    f["sma50"] = c.rolling(50).mean()
    ls = votes(d)["long_state"].to_numpy(int)
    f["trend_prev"] = pd.Series(ls, index=d.index).shift(1).fillna(0).astype(int)
    f["ema20_prev"] = f["ema20"].shift(1)
    prev5 = pd.Series(ls, index=d.index).shift(2).rolling(5).max()
    f["turned"] = (f["trend_prev"] == 1) & (prev5 < 1)
    return f


def walk(f: pd.DataFrame, i: int, fill: float, stop: float, tgt: float, sell: str, max_days: int = 10) -> tuple[float, int]:
    o, h, l, c = (f[k].to_numpy() for k in ("o", "h", "l", "c"))
    ind = f[sell].to_numpy()
    entry = fill * (1 + COST)
    risk = entry - stop
    for k in range(i, min(len(f), i + max_days)):
        if l[k] <= stop:
            px = o[k] if k > i and o[k] <= stop else stop
            return (px * (1 - COST) - entry) / risk, k
        if k > i and h[k] >= tgt:
            px = o[k] if o[k] >= tgt else tgt
            return (px * (1 - COST) - entry) / risk, k
        if c[k] < ind[k]:
            return (c[k] * (1 - COST) - entry) / risk, k
    k = min(len(f), i + max_days) - 1
    return (c[k] * (1 - COST) - entry) / risk, k


def setups(fs: dict, a, b, kind: str) -> list[tuple]:
    out = []
    for s, f in fs.items():
        busy, idx = -1, f.index
        for i in range(1, len(f)):
            t = idx[i]
            if t < a or t > b or i <= busy or not np.isfinite(f["atr"].iloc[i]):
                continue
            r = f.iloc[i]
            if kind == "A":
                e = r.ema20_prev
                if r.trend_prev != 1 or not np.isfinite(e) or f["c"].iloc[i - 1] <= e or r.l > e:
                    continue
                fill = min(r.o, e)
                R, k = walk(f, i, fill, fill - r.atr, fill + 1.5 * r.atr, "sma50")
            else:
                if not r.turned:
                    continue
                fill = r.o
                R, k = walk(f, i, fill, fill - 1.5 * r.atr, fill + 2.0 * r.atr, "ema20")
            out.append((t, s, R))
            busy = k
    return out


def control(fs: dict, real: list, kind: str, seed: int) -> float:
    rng = np.random.default_rng(seed)
    syms = list(fs)
    tot = 0.0
    for t, _, _ in real:
        s = syms[rng.integers(len(syms))]
        f = fs[s]
        if t not in f.index:
            continue
        i = f.index.get_loc(t)
        r = f.iloc[i]
        if not np.isfinite(r.atr):
            continue
        if kind == "A":
            fill = r.o
            R, _ = walk(f, i, fill, fill - r.atr, fill + 1.5 * r.atr, "sma50")
        else:
            fill = r.o
            R, _ = walk(f, i, fill, fill - 1.5 * r.atr, fill + 2.0 * r.atr, "ema20")
        tot += R
    return tot


def summ(rs):
    rs = np.asarray(rs, float)
    if not len(rs):
        return "0 t"
    w, lo = rs[rs > 0].sum(), -rs[rs <= 0].sum()
    return f"{len(rs)} t, win {np.mean(rs > 0):.0%}, PF {(w / lo if lo > 0 else 99):.2f}, {rs.sum():+.1f}R, avg {rs.mean():+.2f}R"


def report(daily_raw: dict, today: date, seeds: int = 20) -> list[str]:
    fs = {s: prep(_dates(d)) for s, d in daily_raw.items() if len(d) > 300}
    fs = {s: f[f.index < pd.Timestamp(today)] for s, f in fs.items()}
    end = max(f.index.max() for f in fs.values())
    out = ["## trendride: the scanner picks an already-trending stock; the test is the TP zone and the sell (10 bps a side, random-stock control x20)"]
    for kind, name in (("A", "PULLBACK to the 20-day EMA, stop 1 ATR, TP zone +1.5 ATR, sell on a close under the 50-day"),
                       ("B", "HOLD from the day it turns trending, stop 1.5 ATR, TP zone +2 ATR, sell on a close under the 20-day EMA")):
        out.append(f"### {kind} {name}")
        for label, a in (("last 2 weeks", pd.Timestamp(today - timedelta(days=14))), ("last 6 months", pd.Timestamp(today - timedelta(days=183)))):
            real = setups(fs, a, end, kind)
            rs = [x[2] for x in real]
            ctrl = [control(fs, real, kind, k) for k in range(seeds)]
            beat = sum(sum(rs) > x for x in ctrl)
            verdict = ""
            if label == "last 2 weeks":
                ok = len(rs) > 0 and sum(rs) > 0 and beat >= round(0.9 * seeds)
                verdict = f" | {'PASS' if ok else 'fail'}" + (" (under 20 trades: thin)" if len(rs) < 20 else "")
            out.append(f"- {label}: {summ(rs)} | random stocks median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds}{verdict}")
    return out
