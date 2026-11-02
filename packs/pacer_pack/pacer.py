"""The "pacer" (owner 2026-10-31, pasted plan): find stocks where momentum is already established, ride them to an ATR-defined TP zone.

PRE-DECLARED (daily bars, the 55 stocks, 10 bps a side):
  PACER at a close: 50-day return minus SPY's in the top 20% of the 55 AND 20-day return minus SPY's > 0 (relative strength leader),
         ADX(14) > 25 (a trend, not a range), volume confirmation = average volume of the last 10 up-days / last 10 down-days >= 1.2 and
         10-day average volume > 50-day average. Entry: the next open, the first time the pacer fires (not in the 5 sessions before).
  EXITS  stop 1.5 ATR(14) under the entry; T1 = +2 ATR: half sold, stop to break-even; T2 = +3 ATR (variant M: the measured move =
         entry + the prior 20-day high-low range, at least +2.5 ATR) for the rest; virtual trail after +1R keeps half the best gain
         (the deployed profit lock); 20 sessions max.
  CONTROL the same entry days on random stocks of the 55 (same exits, 20 seeds), and SPY bought on the same days.
  READ    since 2023 (about 3.5 years), each year, the last 6 months and the last 2 weeks. PASS: since 2023 net R > 0, PF >= 1.2, beats 18/20
          random runs AND SPY, and the last 12 months net R > 0.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .daytp import adx
from .trendride import _dates

COST = 10.0 / 1e4


def prep(d: pd.DataFrame, spy: pd.Series) -> pd.DataFrame:
    o, h, l, c, v = (d[k] for k in ("open", "high", "low", "close", "volume"))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame({"o": o, "h": h, "l": l, "c": c}, index=d.index)
    f["atr"] = tr.rolling(14).mean()
    sc = spy.reindex(d.index).ffill()
    f["rs50"] = c / c.shift(50) - sc / sc.shift(50)
    f["rs20"] = c / c.shift(20) - sc / sc.shift(20)
    f["adx"] = adx(d)
    up = c > c.shift()
    upv = v.where(up).rolling(30, min_periods=10).apply(lambda x: np.nanmean(x[~np.isnan(x)][-10:]) if np.sum(~np.isnan(x)) >= 5 else np.nan, raw=True)
    dnv = v.where(~up).rolling(30, min_periods=10).apply(lambda x: np.nanmean(x[~np.isnan(x)][-10:]) if np.sum(~np.isnan(x)) >= 5 else np.nan, raw=True)
    f["vol_ok"] = (upv / dnv >= 1.2) & (v.rolling(10).mean() > v.rolling(50).mean())
    f["rng20"] = h.rolling(20).max() - l.rolling(20).min()
    return f


def walk(f: pd.DataFrame, i: int, measured: bool, max_days: int = 20) -> tuple[float, int]:
    """i = signal bar; entry at bar i+1 open. R = P&L / initial risk on the whole position."""
    o, h, l, c = (f[k].to_numpy() for k in ("o", "h", "l", "c"))
    j = i + 1
    if j >= len(f):
        return None, i
    atr = f["atr"].iloc[i]
    entry = o[j] * (1 + COST)
    risk = 1.5 * atr
    stop = entry - risk
    t1 = entry + 2 * atr
    t2 = max(entry + f["rng20"].iloc[i], entry + 2.5 * atr) if measured else entry + 3 * atr
    half_done, realized, peak = False, 0.0, entry
    for k in range(j, min(len(f), j + max_days)):
        size = 0.5 if half_done else 1.0
        lock = entry + 0.5 * (peak - entry) if peak - entry >= risk else None
        eff = max(stop, lock) if lock is not None else stop
        if l[k] <= eff:
            px = o[k] if k > j and o[k] <= eff else eff
            return realized + size * (px * (1 - COST) - entry) / risk, k
        if not half_done and k > j and h[k] >= t1:
            realized += 0.5 * (max(o[k], t1) * (1 - COST) - entry) / risk
            half_done, stop = True, max(stop, entry)
        if half_done and k > j and h[k] >= t2:
            px = o[k] if o[k] >= t2 else t2
            return realized + 0.5 * (px * (1 - COST) - entry) / risk, k
        peak = max(peak, h[k] if k > j else c[k])
    k = min(len(f), j + max_days) - 1
    return realized + (0.5 if half_done else 1.0) * (c[k] * (1 - COST) - entry) / risk, k


def signals(fs: dict) -> list[tuple]:
    rs = pd.DataFrame({s: f["rs50"] for s, f in fs.items()})
    top = rs.rank(axis=1, pct=True) >= 0.8
    out = []
    for s, f in fs.items():
        fire = (top[s].reindex(f.index).fillna(False) & (f["rs20"] > 0) & (f["adx"] > 25) & f["vol_ok"].fillna(False)).to_numpy()
        for i in range(60, len(f) - 1):
            if fire[i] and not fire[max(0, i - 5):i].any():
                out.append((f.index[i], s, i))
    return sorted(out)


def run(fs, sigs, a, b, measured, rng=None, spy_f=None):
    rs, busy, syms = [], {}, list(fs)
    for t, s, i in sigs:
        if t < a or t > b:
            continue
        if spy_f is not None:
            s, f = "SPY", spy_f
            if t not in f.index:
                continue
            i = f.index.get_loc(t)
        elif rng is not None:
            s = syms[rng.integers(len(syms))]
            f = fs[s]
            if t not in f.index:
                continue
            i = f.index.get_loc(t)
        else:
            f = fs[s]
        if i <= busy.get(s, -1) or not np.isfinite(f["atr"].iloc[i]):
            continue
        R, k = walk(f, i, measured)
        if R is None:
            continue
        busy[s] = k
        rs.append((t, R))
    return rs


def summ(rs):
    x = np.asarray([r for _, r in rs], float)
    if not len(x):
        return "0 t"
    w, lo = x[x > 0].sum(), -x[x <= 0].sum()
    return f"{len(x)} t, win {np.mean(x > 0):.0%}, PF {(w / lo if lo > 0 else 99):.2f}, {x.sum():+.1f}R"


def report(daily_raw: dict, today: date, seeds: int = 20) -> list[str]:
    spy_d = _dates(daily_raw["SPY"])
    spy_d = spy_d[spy_d.index < pd.Timestamp(today)]
    fs = {s: prep(_dates(d)[lambda x: x.index < pd.Timestamp(today)], spy_d["close"]) for s, d in daily_raw.items()
          if s not in ("SPY", "QQQ") and len(d) > 300}
    spy_f = prep(spy_d, spy_d["close"])
    sigs = signals(fs)
    end = max(f.index.max() for f in fs.values())
    out = [f"## pacer: relative-strength leaders (top 20% 50-day RS vs SPY, 20-day RS > 0, ADX > 25, up-day volume) ridden to ATR TP zones; "
           f"{len(fs)} stocks, {len(sigs)} pacer signals, 10 bps a side"]
    for measured, name in ((False, "T2 = +3 ATR"), (True, "T2 = measured move (20-day range, at least +2.5 ATR)")):
        out.append(f"### {name} (T1 +2 ATR half off, stop 1.5 ATR -> break-even, trail after +1R, 20 sessions max)")
        verdict = None
        for label, a in (("since 2023", pd.Timestamp("2023-01-01")), ("2023", pd.Timestamp("2023-01-01")), ("2024", pd.Timestamp("2024-01-01")),
                         ("2025", pd.Timestamp("2025-01-01")), ("last 12 months", pd.Timestamp(today - timedelta(days=365))),
                         ("last 6 months", pd.Timestamp(today - timedelta(days=183))), ("last 2 weeks", pd.Timestamp(today - timedelta(days=14)))):
            b = pd.Timestamp(f"{label}-12-31") if label.isdigit() else end
            real = run(fs, sigs, a, b, measured)
            line = f"- {label}: {summ(real)}"
            if label in ("since 2023", "last 12 months", "last 2 weeks"):
                ctrl = [sum(r for _, r in run(fs, sigs, a, b, measured, rng=np.random.default_rng(k))) for k in range(seeds)]
                spy = run(fs, sigs, a, b, measured, spy_f=spy_f)
                beat = sum(sum(r for _, r in real) > x for x in ctrl)
                line += f" | random stocks median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds} | SPY same days {summ(spy)}"
                x = np.asarray([r for _, r in real], float)
                if label == "since 2023":
                    lo = -x[x <= 0].sum()
                    verdict = [x.sum() > 0, (x[x > 0].sum() / lo if lo > 0 else 99) >= 1.2, beat >= round(0.9 * seeds),
                               x.sum() > sum(r for _, r in spy)]
                if label == "last 12 months":
                    verdict.append(x.sum() > 0)
            out.append(line)
        out.append(f"- verdict: {'PASS' if verdict and all(verdict) else 'fail'}")
    return out
