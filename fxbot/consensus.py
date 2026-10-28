"""Consensus buy / sell zones (owner 2026-10-28: "analyze where all the experts recommend to buy, average them together to get the optimal buy zone; then
where all the trending experts recommend sell, average them and get the optimal sell zone; then retest 2 weeks and report").

PRE-DECLARED before any run:
- Where an expert buys = the 1-hour z-score at each of its LONG signals: z = (close - 20-hour average) / ATR(14), on the 1-hour bar at or just before
  its signal bar's close (one common yardstick for 1-hour, 4-hour and daily experts). Where a trending expert sells = the same at its SHORT signals.
- Experts: every OANDA runner strategy plus the book methods on 1-hour / 4-hour / daily bars (the 5- and 15-minute-only methods are left out). TRENDING:
  confirm_h1, cog_h4, Holy Grail, TTM squeeze, MACD + 200 EMA, Williams volatility breakout, inside-bar breakout. An expert needs 20+ signals to count.
- Calibration: the 12 months before the test window (nothing from the test window). Each expert counts once: BUY LEVEL = the average of the experts'
  median long-signal z; SELL LEVEL = the average of the trending experts' median short-signal z. Each zone = its level +- 0.25 ATR. (A first draft used
  the averaged 25th-75th percentile bands; on a synthetic dry run those bands overlapped, so the zones are the averaged medians, set before any real run.)
- Retest, last 14 days, 7 majors, 1-hour bid/ask bars: BUY when a 1-hour close lands inside the buy zone (fill at the next open, ask), SELL (exit) when a
  close reaches the sell zone's lower edge, stop 1.5 ATR under the fill, 48 bars at most. The mirror SHORT: sell short inside the sell zone, cover at the
  buy zone's upper edge. Results at the normal and 2x spread, against the same rules on the same bars shuffled into random order (3 seeds).
- FALLBACK, declared now: trend experts SELL breakdowns, so their sell zone may sit BELOW the buy zone (nothing to sell into). If the zones overlap
  or invert, the retest also runs variant B: SELL ZONE = where ALL experts sell (their short-signal z), the mirror of the buy zone.
Nothing trades on this; it is a test."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .data import mid

TRENDING = ("confirm_h1", "cog_h4", "raschke_holy_grail", "carter_ttm_squeeze", "macd_200ema", "williams_vol_breakout", "inside_bar_breakout")
STOP_ATR, MAX_BARS, MIN_SIGNALS, HALF_WIDTH = 1.5, 48, 20, 0.25


def experts() -> dict:
    """name -> (factory, granularity)."""
    from . import live
    from .methods import METHODS
    out = {k: (v["make"], v["gran"]) for k, v in live.STRATS.items() if v.get("make")}
    for k, (make, grans) in METHODS.items():
        g = next((x for x in ("H1", "H4", "D") if x in grans), None)
        if g:
            out[k] = (make, g)
    return out


def zscore(m1: pd.DataFrame) -> pd.Series:
    c = m1["close"]
    pc = c.shift(1)
    atr = pd.concat([m1["high"] - m1["low"], (m1["high"] - pc).abs(), (m1["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean()
    return (c - c.rolling(20).mean()) / atr


def signal_z(make, bars: pd.DataFrame, z1: pd.Series, a: date, b: date, gran_minutes: int) -> tuple[list, list]:
    """z (1-hour yardstick) at this expert's long and short signals with signal dates in [a, b]."""
    f = make().frame(mid(bars))
    out = ([], [])
    for k, col in enumerate(("long", "short")):
        when = f.index[f[col].fillna(False).to_numpy(bool)] + pd.Timedelta(minutes=gran_minutes)    # the signal bar's close
        when = when[(when.date >= a) & (when.date <= b)]
        if len(when):
            pos = z1.index.searchsorted(when, side="right") - 1
            v = z1.to_numpy()[pos[pos >= 0]]
            out[k].extend(float(x) for x in v if np.isfinite(x))
    return out


def zones(per_expert: dict) -> dict:
    """per_expert: name -> (long z list, short z list). Returns the zones and each expert's own spread."""
    rows, buys, sells = {}, [], []
    for name, (lz, sz) in per_expert.items():
        r = {}
        if len(lz) >= MIN_SIGNALS:
            r["buy"] = (float(np.percentile(lz, 25)), float(np.median(lz)), float(np.percentile(lz, 75)), len(lz))
            buys.append(r["buy"])
        if name in TRENDING and len(sz) >= MIN_SIGNALS:
            r["sell"] = (float(np.percentile(sz, 25)), float(np.median(sz)), float(np.percentile(sz, 75)), len(sz))
            sells.append(r["sell"])
        rows[name] = r
    alls = [float(np.median(sz_)) for _, (_, sz_) in per_expert.items() if len(sz_) >= MIN_SIGNALS]
    zone = lambda c: (c - HALF_WIDTH, c + HALF_WIDTH)   # noqa: E731
    bz = zone(float(np.mean([x[1] for x in buys]))) if buys else None
    sz = zone(float(np.mean([x[1] for x in sells]))) if sells else None
    sz_all = zone(float(np.mean(alls))) if alls else None
    return {"buy_zone": bz, "sell_zone": sz, "sell_zone_all": sz_all, "experts": rows, "n_buy_experts": len(buys), "n_sell_experts": len(sells),
            "n_sell_all": len(alls)}


def trade_zones(df: pd.DataFrame, bz: tuple, sz: tuple, a: date, b: date, side_filter: int = 0) -> list[dict]:
    """1-hour bid/ask bars. Long: close in the buy zone -> buy next open; out when z >= sell zone's lower edge (next open), at the stop, or after MAX_BARS.
    Short mirrors it. side_filter: +1 longs only, -1 shorts only, 0 both. Returns trades in R (initial stop distance)."""
    m = mid(df)
    z = zscore(m).to_numpy(float)
    c = m["close"].to_numpy(float)
    pc = m["close"].shift(1)
    atr = pd.concat([m["high"] - m["low"], (m["high"] - pc).abs(), (m["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().to_numpy(float)
    bo, bh, bl, ao, ah, al = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    dates = np.asarray(df.index.date)
    out, i, n = [], 20, len(df)
    while i < n - 1:
        side = 0
        if not (a <= dates[i] <= b) or not np.isfinite(z[i]) or not np.isfinite(atr[i]):
            i += 1
            continue
        if side_filter >= 0 and bz[0] <= z[i] <= bz[1]:
            side = 1
        elif side_filter <= 0 and sz[0] <= z[i] <= sz[1]:
            side = -1
        if not side or (side == 1 and bz[1] >= sz[0]) or (side == -1 and sz[0] <= bz[1]):
            i += 1                                       # no trade when the zones overlap: nothing to sell into
            continue
        j = i + 1
        fill = ao[j] if side == 1 else bo[j]
        sd = STOP_ATR * atr[i]
        stop = fill - side * sd
        k, px = j, None
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            if k > j and side * (op - stop) <= 0:
                px = op
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px = stop
            elif (side == 1 and np.isfinite(z[k]) and z[k] >= sz[0]) or (side == -1 and np.isfinite(z[k]) and z[k] <= bz[1]) or k - j + 1 >= MAX_BARS:
                if k + 1 < n:
                    k += 1
                    px = bo[k] if side == 1 else ao[k]
                else:
                    px = c[k]
            elif k == n - 1:
                px = c[k]
            if px is not None:
                break
            k += 1
        out.append({"date": dates[j], "side": side, "r": side * (px - fill) / sd})
        i = max(k, i + 1)
    return out


def summary(tr: list[dict]) -> dict:
    rs = [t["r"] for t in tr]
    if not rs:
        return {"trades": 0, "win": 0.0, "pf": None, "net_r": 0.0, "avg_r": 0.0}
    w, lo = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    return {"trades": len(rs), "win": len(w) / len(rs), "pf": (sum(w) / -sum(lo)) if lo and sum(lo) < 0 else 99.0, "net_r": sum(rs),
            "avg_r": sum(rs) / len(rs)}


def fmt(s: dict) -> str:
    pf = "n/a" if s["pf"] is None else f"{s['pf']:.2f}"
    return f"{s['trades']} trades, win {s['win']:.0%}, PF {pf}, net {s['net_r']:+.1f}R, avg {s['avg_r']:+.3f}R"


def windows(today: date) -> dict:
    test_a = today - timedelta(days=14)
    return {"calibrate": (test_a - timedelta(days=365), test_a - timedelta(days=1)), "test": (test_a, today)}
