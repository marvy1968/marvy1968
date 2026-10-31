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


# ---- pullback follow-up (owner 2026-10-31 "how about our new idea"): the A entries beat random stocks 20/20 over 6 months but only broke
# even, so the work is the TP zone and the sell. PRE-DECLARED grid on the SAME A entries (pullback to the 20-day EMA in a trending stock):
#   stop 1.0 / 1.5 ATR  x  TP zone +1 / +1.5 / +2 / +3 ATR / none  x  sell on a close under the 20-day EMA / the 50-day / none, 10 sessions max.
# Chosen on the first 3 months, confirmed on the last 3 (unseen): adopted only if net R > 0 and PF >= 1.2 on the unseen half AND it beats
# random stocks with the same exits 18/20 there.
def walk2(f, i, fill, stop, tgt, sell, max_days=10):
    o, h, l, c = (f[k].to_numpy() for k in ("o", "h", "l", "c"))
    ind = f[sell].to_numpy() if sell else None
    entry = fill * (1 + COST)
    risk = entry - stop
    for k in range(i, min(len(f), i + max_days)):
        if l[k] <= stop:
            px = o[k] if k > i and o[k] <= stop else stop
            return (px * (1 - COST) - entry) / risk, k
        if tgt is not None and k > i and h[k] >= tgt:
            px = o[k] if o[k] >= tgt else tgt
            return (px * (1 - COST) - entry) / risk, k
        if ind is not None and c[k] < ind[k]:
            return (c[k] * (1 - COST) - entry) / risk, k
    k = min(len(f), i + max_days) - 1
    return (c[k] * (1 - COST) - entry) / risk, k


def pullback_entries(fs, a, b):
    out = []
    for s, f in fs.items():
        for i in range(1, len(f)):
            t = f.index[i]
            if t < a or t > b or not np.isfinite(f["atr"].iloc[i]):
                continue
            r = f.iloc[i]
            e = r.ema20_prev
            if r.trend_prev != 1 or not np.isfinite(e) or f["c"].iloc[i - 1] <= e or r.l > e:
                continue
            out.append((s, i, min(r.o, e), r.atr))
    return out


def grade(fs, entries, stop_k, tp_k, sell, rng=None):
    rs, busy = [], {}
    syms = list(fs)
    for s, i, fill, atr in entries:
        if rng is not None:                                   # control: a random stock on the same day, bought at its open
            s2 = syms[rng.integers(len(syms))]
            f2 = fs[s2]
            t = fs[s].index[i]
            if t not in f2.index:
                continue
            i2 = f2.index.get_loc(t)
            r2 = f2.iloc[i2]
            if not np.isfinite(r2.atr):
                continue
            s, i, fill, atr = s2, i2, r2.o, r2.atr
        if i <= busy.get(s, -1):
            continue
        R, k = walk2(fs[s], i, fill, fill - stop_k * atr, None if tp_k is None else fill + tp_k * atr, sell)
        busy[s] = k
        rs.append(R)
    return rs


def pullback_report(daily_raw: dict, today: date, seeds: int = 20) -> list[str]:
    fs = {s: prep(_dates(d)) for s, d in daily_raw.items() if len(d) > 300}
    fs = {s: f[f.index < pd.Timestamp(today)] for s, f in fs.items()}
    end = max(f.index.max() for f in fs.values())
    a0, mid = pd.Timestamp(today - timedelta(days=183)), pd.Timestamp(today - timedelta(days=91))
    first, second = pullback_entries(fs, a0, mid - timedelta(days=1)), pullback_entries(fs, mid, end)
    out = [f"## trendride pullback follow-up: {len(first)} entries {a0.date()}..{mid.date()} (choose) / {len(second)} entries {mid.date()}..{end.date()} (confirm)"]
    rows = []
    for sk in (1.0, 1.5):
        for tk in (1.0, 1.5, 2.0, 3.0, None):
            for sell in ("ema20", "sma50", None):
                r1 = grade(fs, first, sk, tk, sell)
                rows.append((sum(r1), sk, tk, sell, r1))
    rows.sort(key=lambda x: -x[0])
    out.append("top 5 on the first 3 months (stop ATR / TP ATR / sell rule): " + "; ".join(
        f"{sk}/{tk}/{sell} {summ(r1)}" for _, sk, tk, sell, r1 in rows[:5]))
    _, sk, tk, sell, r1 = rows[0]
    r2 = grade(fs, second, sk, tk, sell)
    ctrl = [sum(grade(fs, second, sk, tk, sell, np.random.default_rng(k))) for k in range(seeds)]
    beat = sum(sum(r2) > x for x in ctrl)
    w, lo = sum(x for x in r2 if x > 0), -sum(x for x in r2 if x <= 0)
    pf = w / lo if lo > 0 else 99
    ok = sum(r2) > 0 and pf >= 1.2 and beat >= round(0.9 * seeds)
    base = grade(fs, second, 1.0, 1.5, "sma50")
    out.append(f"- chosen {sk} ATR stop / TP {tk} ATR / sell {sell}: unseen last 3 months {summ(r2)} | random stocks median {np.median(ctrl):+.1f}R, "
               f"beaten {beat}/{seeds} | yesterday's version on the same months {summ(base)} | {'PASS' if ok else 'fail'}")
    return out
