"""Trend-angle pacer (owner 2026-11-01, spec from Gemini/ChatGPT, copy on the VM in ~/research/trend_angle_pacer/).

PRE-DECLARED before any run (daily bars, the 55 stocks, long AND short; nothing is fitted, so 2019-2023 is only shown for reference):
  SWING   = ATR(14), the typical daily swing.
  ANGLE   = slope of a 20-bar least-squares line through the closes, in ATRs per bar. Trend up: angle >= +0.10 (2 ATR over 20 bars);
            down: angle <= -0.10.
  HIGHER TIMEFRAME = completed weekly bars (W-FRI): the 20-week slope in weekly ATRs per bar must have the same sign and be >= 0.05 in size.
  FLATTENING BLOCK = no entry when the angle has lost more than half its value over the last 5 bars (|angle| < 0.5 x |angle 5 bars ago|).
  ZONE    = uptrend pullback: the day's low reached the 20 EMA + 0.25 ATR or lower, and the close is no more than 1 ATR under the 20 EMA;
            downtrend bounce: mirror image. Entry at the next open.
  STOP    = at least 1.75 ATR from entry, wider to the 10-bar low - 0.25 ATR (short: 10-bar high + 0.25 ATR), never wider than 3 ATR.
  TP ZONE = the 20-bar line projected 10 bars ahead (fitted value now + 10 x slope); skipped if that is less than 0.5 ATR past the entry.
            Time exit at the 10th close. A bar that touches both stop and TP counts as the stop (conservative). Gaps fill at the open.
  COSTS   = 10 bps a side, then 20 bps a side (double).
  ONE trade per stock at a time.
  CONTROL = random entries: the same days and side, a random stock of the 55, the same stop and TP distances in ATRs, same time exit, 20 seeds.
  HOLDOUT = 2024-01-01 to now.
  PASS    = on the holdout: 100+ trades, profit factor >= 1.10 after 10 bps, beats 16+/20 random runs, net > 0 at 20 bps.
"""

from datetime import date

import numpy as np
import pandas as pd

from .trendride import _dates

COST1, COST2 = 10.0 / 1e4, 20.0 / 1e4
N, AMIN, WMIN, DECAY, HORIZON = 20, 0.10, 0.05, 0.5, 10
HOLDOUT = pd.Timestamp("2024-01-01")


def _atr(h, l, c, n=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _slope(c: pd.Series, n: int = N) -> tuple[pd.Series, pd.Series]:
    """Rolling least-squares slope (per bar) and the fitted value at the last bar."""
    x = np.arange(n, dtype=float)
    xc = x - x.mean()
    w = xc / (xc ** 2).sum()
    slope = c.rolling(n).apply(lambda y: float(np.dot(w, y)), raw=True)
    fit = c.rolling(n).mean() + slope * (n - 1) / 2
    return slope, fit


def prep(d: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = (d[k] for k in ("open", "high", "low", "close"))
    f = pd.DataFrame({"o": o, "h": h, "l": l, "c": c}, index=d.index)
    f["atr"] = _atr(h, l, c)
    slope, fit = _slope(c)
    f["angle"] = slope / f["atr"]
    f["fit"], f["slope"] = fit, slope
    f["ema"] = c.ewm(span=20, adjust=False).mean()
    f["lo10"], f["hi10"] = l.rolling(10).min(), h.rolling(10).max()
    wk = d.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    ws, _ = _slope(wk["close"])
    wang = (ws / _atr(wk["high"], wk["low"], wk["close"])).rename("wangle")
    # a weekly bar labelled Friday is complete at that Friday's close: use it from that day on (never earlier)
    f["wangle"] = wang.reindex(f.index.union(wang.index)).ffill().reindex(f.index)
    f.loc[f.index < wang.index.min(), "wangle"] = np.nan
    return f


def signals(f: pd.DataFrame) -> np.ndarray:
    """+1 long / -1 short / 0 at each close."""
    a, a5, wa = f["angle"], f["angle"].shift(5), f["wangle"]
    flat = a.abs() < DECAY * a5.abs()
    up = (a >= AMIN) & (wa >= WMIN) & ~flat & (f["l"] <= f["ema"] + 0.25 * f["atr"]) & (f["c"] >= f["ema"] - f["atr"])
    dn = (a <= -AMIN) & (wa <= -WMIN) & ~flat & (f["h"] >= f["ema"] - 0.25 * f["atr"]) & (f["c"] <= f["ema"] + f["atr"])
    return np.where(up.fillna(False), 1, np.where(dn.fillna(False), -1, 0))


def levels(f: pd.DataFrame, i: int, side: int) -> tuple[float, float] | None:
    """Stop and TP distances from the next open, in ATRs (computed at the signal close, applied to the actual entry)."""
    atr, ref = f["atr"].iloc[i], f["c"].iloc[i]
    struct = (ref - (f["lo10"].iloc[i] - 0.25 * atr)) if side == 1 else ((f["hi10"].iloc[i] + 0.25 * atr) - ref)
    stop_k = float(np.clip(struct / atr, 1.75, 3.0))
    tp_px = f["fit"].iloc[i] + HORIZON * f["slope"].iloc[i]
    tp_k = side * (tp_px - ref) / atr
    if not np.isfinite(tp_k) or not np.isfinite(stop_k):
        return None
    return stop_k, tp_k


def walk(f: pd.DataFrame, i: int, side: int, stop_k: float, tp_k: float, cost: float) -> tuple[float | None, int]:
    o, h, l, c = (f[k].to_numpy() for k in ("o", "h", "l", "c"))
    j = i + 1
    if j >= len(f) or tp_k < 0.5:
        return None, i
    atr = f["atr"].iloc[i]
    entry = o[j]
    if tp_k * atr - side * (entry - f["c"].iloc[i]) < 0.5 * atr:   # the open already ran through most of the TP zone
        return None, i
    stop = entry - side * stop_k * atr
    tp = f["c"].iloc[i] + side * tp_k * atr
    risk = stop_k * atr
    e_cost = entry * (1 + side * cost)
    for k in range(j, min(len(f), j + HORIZON)):
        hit_stop = l[k] <= stop if side == 1 else h[k] >= stop
        hit_tp = h[k] >= tp if side == 1 else l[k] <= tp
        if hit_stop:
            px = o[k] if (k > j and side * (o[k] - stop) <= 0) else stop
        elif hit_tp:
            px = o[k] if (k > j and side * (o[k] - tp) >= 0) else tp
        else:
            continue
        return side * (px * (1 - side * cost) - e_cost) / risk, k
    k = min(len(f), j + HORIZON) - 1
    return side * (c[k] * (1 - side * cost) - e_cost) / risk, k


def trades(fs: dict, sig: dict, a, b, cost: float, rng=None) -> list[tuple]:
    events = []
    for s, f in fs.items():
        for i in np.flatnonzero(sig[s]):
            t = f.index[i]
            if a <= t <= b and i + 1 < len(f):
                events.append((t, s, int(i), int(sig[s][i])))
    events.sort()
    syms, busy, out = list(fs), {}, []
    for t, s, i, side in events:
        lv = levels(fs[s], i, side)
        if lv is None:
            continue
        if rng is not None:
            s = syms[rng.integers(len(syms))]
            if t not in fs[s].index:
                continue
            i = fs[s].index.get_loc(t)
            if not np.isfinite(fs[s]["atr"].iloc[i]):
                continue
        if i <= busy.get(s, -1):
            continue
        R, k = walk(fs[s], i, side, *lv, cost)
        if R is None:
            continue
        busy[s] = k
        out.append((t, s, side, R))
    return out


def stats(rs) -> dict:
    x = np.asarray([r[3] for r in rs], float)
    if not len(x):
        return {"n": 0, "win": 0.0, "pf": 0.0, "net": 0.0}
    lo = -x[x <= 0].sum()
    return {"n": len(x), "win": float(np.mean(x > 0)), "pf": float(x[x > 0].sum() / lo) if lo > 0 else 99.0, "net": float(x.sum())}


def fmt(s: dict) -> str:
    return f"{s['n']} t, win {s['win']:.0%}, PF {s['pf']:.2f}, {s['net']:+.1f}R"


def report(daily_raw: dict, today: date, seeds: int = 20) -> list[str]:
    fs = {}
    for s, d in daily_raw.items():
        if s in ("SPY", "QQQ") or len(d) < 400:
            continue
        d = _dates(d)
        fs[s] = prep(d[d.index < pd.Timestamp(today)])
    sig = {s: signals(f) for s, f in fs.items()}
    end = max(f.index.max() for f in fs.values())
    start = min(f.index.min() for f in fs.values())
    out = [f"## anglepacer: 20-bar trend angle (ATRs/bar) + weekly agreement + flattening block; pullback/bounce entries; stop >= 1.75 ATR; "
           f"TP = 20-bar line projected 10 bars; {len(fs)} stocks, data {start.date()}..{end.date()}",
           "| period | cost | trades | win | PF | net R | long | short | random median / beaten |", "|---|---|---|---|---|---|---|---|---|"]
    verdict = {}
    periods = [("pre-2024 (reference)", start, HOLDOUT - pd.Timedelta(days=1)), ("HOLDOUT 2024-now", HOLDOUT, end),
               ("  2024", HOLDOUT, pd.Timestamp("2024-12-31")), ("  2025", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
               ("  2026", pd.Timestamp("2026-01-01"), end)]
    for label, a, b in periods:
        for cost, cl in ((COST1, "10 bps"), (COST2, "20 bps")):
            rs = trades(fs, sig, a, b, cost)
            s = stats(rs)
            lg, sh = stats([r for r in rs if r[2] == 1]), stats([r for r in rs if r[2] == -1])
            ctrl = ""
            if cost == COST1 and label in ("pre-2024 (reference)", "HOLDOUT 2024-now"):
                c = [stats(trades(fs, sig, a, b, cost, rng=np.random.default_rng(k)))["net"] for k in range(seeds)]
                beat = sum(s["net"] > x for x in c)
                ctrl = f"{np.median(c):+.1f}R / {beat}/{seeds}"
                if label.startswith("HOLDOUT"):
                    verdict.update(n=s["n"], pf=s["pf"], beat=beat)
            if cost == COST2 and label.startswith("HOLDOUT"):
                verdict["net2"] = s["net"]
            out.append(f"| {label} | {cl} | {s['n']} | {s['win']:.0%} | {s['pf']:.2f} | {s['net']:+.1f} | {fmt(lg)} | {fmt(sh)} | {ctrl} |")
    checks = [("100+ trades", verdict["n"] >= 100), ("PF >= 1.10 at 10 bps", verdict["pf"] >= 1.10),
              (f"beats 16+/{seeds} random", verdict["beat"] >= round(0.8 * seeds)), ("net > 0 at 20 bps", verdict["net2"] > 0)]
    out.append("- pass bar (holdout): " + ", ".join(f"{k} {'YES' if v else 'no'}" for k, v in checks))
    out.append(f"- verdict: {'PASS' if all(v for _, v in checks) else 'FAIL'}")
    last = {s: (f.index[-1], int(sig[s][-1])) for s, f in fs.items() if sig[s][-1] != 0}
    if last:
        out.append("- signals at the last close: " + ", ".join(f"{s} {'LONG' if v == 1 else 'SHORT'}" for s, (_, v) in sorted(last.items())))
    return out
