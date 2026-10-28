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


# ------------------------------------------------------------------ separate trend / reversion zones on the DAILY range (owner 2026-10-28)
# PRE-DECLARED before any run. Yardstick: zd = (1-hour close - 20-day average of completed daily closes) / ATR14 of completed daily bars (daily bars built
# from the 1-hour bars at the 17:00 New York roll). REVERSION group (every non-trend expert with 20+ signals): BUY ZONE = their average median long zd
# +- 0.25, SELL ZONE = their average median short zd +- 0.25; long in the buy zone, out when zd reaches the sell zone, short the mirror. TREND group
# (TRENDING with 20+ signals): BUY ZONE = their average median long zd +- 0.25 (strength), SELL ZONE = their average median short zd +- 0.25 (weakness);
# long in the buy zone, out when zd falls back to the sell zone, short the mirror. Stop 1.5 daily ATR, 20 days at most, one trade per pair, signals on
# 1-hour closes, fills at the next 1-hour open (bid/ask). Calibration: the 12 months before the last 92 days. Tests: last 14 days, and last 92 days for
# context (both unseen). Normal and 2x spread; the same rules on 1-hour bars shuffled into random order (3 seeds) as the control.
DAILY_STOP_ATR, DAILY_MAX_BARS = 1.5, 480


def daily_from_h1(m1: pd.DataFrame) -> pd.DataFrame:
    day = (m1.index.tz_convert("America/New_York") + pd.Timedelta(hours=7)).date
    g = m1.groupby(day)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(), "n": g.size()})
    return d[d["n"] >= 2]


def zscore_daily(m1: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(zd, daily ATR) on the 1-hour index, from completed daily bars only."""
    d = daily_from_h1(m1)
    pc = d["close"].shift(1)
    atr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean()
    sma = d["close"].rolling(20).mean()
    day = (m1.index.tz_convert("America/New_York") + pd.Timedelta(hours=7)).date
    prev = pd.Series(range(len(d)), index=d.index)
    pos = np.array([prev.get(x, -1) for x in day]) - 1                    # yesterday's completed bar
    idx_ok = pos >= 0
    s_sma, s_atr = np.full(len(m1), np.nan), np.full(len(m1), np.nan)
    s_sma[idx_ok], s_atr[idx_ok] = sma.to_numpy()[pos[idx_ok]], atr.to_numpy()[pos[idx_ok]]
    datr = pd.Series(s_atr, index=m1.index)
    return (m1["close"] - s_sma) / datr, datr


def group_zones(per_expert: dict) -> dict:
    out = {}
    for grp, members in (("trend", lambda n: n in TRENDING), ("reversion", lambda n: n not in TRENDING)):
        longs = [float(np.median(lz)) for n, (lz, sz) in per_expert.items() if members(n) and len(lz) >= MIN_SIGNALS]
        shorts = [float(np.median(sz)) for n, (lz, sz) in per_expert.items() if members(n) and len(sz) >= MIN_SIGNALS]
        out[grp] = {"buy": (np.mean(longs) - HALF_WIDTH, np.mean(longs) + HALF_WIDTH) if longs else None,
                    "sell": (np.mean(shorts) - HALF_WIDTH, np.mean(shorts) + HALF_WIDTH) if shorts else None, "n_buy": len(longs), "n_sell": len(shorts)}
    return out


def trade_group(df: pd.DataFrame, mode: str, bz: tuple, sz: tuple, a: date, b: date, pair: str = "") -> list[dict]:
    """mode 'reversion' (buy zone below the sell zone) or 'trend' (buy zone above it). See the block comment. Financing is charged like the backtest
    engine (FIN_PER_NIGHT of the notional per 17:00 New York rollover held, Wednesday x3) - multi-day holds pay it (added 2026-10-28, blind-spot review)."""
    from .engine import FIN_PER_NIGHT, _nights
    m = mid(df)
    zd, datr = zscore_daily(m)
    z, at = zd.to_numpy(float), datr.to_numpy(float)
    c = m["close"].to_numpy(float)
    bo, bh, bl, ao, ah, al = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    dates = np.asarray(df.index.date)
    if (mode == "reversion" and not bz[1] < sz[0]) or (mode == "trend" and not sz[1] < bz[0]):
        return []
    out, i, n = [], 0, len(df)
    while i < n - 1:
        if not (a <= dates[i] <= b) or not np.isfinite(z[i]) or not np.isfinite(at[i]):
            i += 1
            continue
        side = 1 if bz[0] <= z[i] <= bz[1] else -1 if sz[0] <= z[i] <= sz[1] else 0
        if not side:
            i += 1
            continue
        j = i + 1
        fill = ao[j] if side == 1 else bo[j]
        sd = DAILY_STOP_ATR * at[i]
        stop = fill - side * sd
        k, px = j, None
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            if mode == "reversion":
                done = (side == 1 and z[k] >= sz[0]) or (side == -1 and z[k] <= bz[1])
            else:
                done = (side == 1 and z[k] <= sz[1]) or (side == -1 and z[k] >= bz[0])
            if k > j and side * (op - stop) <= 0:
                px = op
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px = stop
            elif (np.isfinite(z[k]) and done) or k - j + 1 >= DAILY_MAX_BARS:
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
        kk = min(k, n - 1)
        nts = _nights(df.index[j], df.index[kk])
        out.append({"date": dates[j], "side": side, "r": (side * (px - fill) - nts * FIN_PER_NIGHT * fill) / sd, "pair": pair, "nights": nts})
        i = max(k, i + 1)
    return out


def signal_times(make, bars: pd.DataFrame, gran_minutes: int) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Close times of this expert's long and short signal bars (whole history)."""
    f = make().frame(mid(bars))
    return tuple(f.index[f[col].fillna(False).to_numpy(bool)] + pd.Timedelta(minutes=gran_minutes) for col in ("long", "short"))


def z_at(times: pd.DatetimeIndex, z: pd.Series) -> pd.Series:
    """zd at each signal time (the 1-hour bar at or just before it), indexed by the signal time."""
    pos = z.index.searchsorted(times, side="right") - 1
    ok = pos >= 0
    return pd.Series(z.to_numpy()[pos[ok]], index=times[ok]).dropna()


# Rolling walk-forward (owner 2026-10-28 "keep going", after the 92-day trend result): PRE-DECLARED. Every calendar quarter from 2022 Q1: the zones are
# re-set from the 12 months BEFORE the quarter (each expert's median zd, group averages, +-0.25), then the quarter is traded with them (unseen), at the
# normal and 2x spread; the same on 1-hour bars shuffled into random order (3 seeds). PASS for a group: over all quarters at 2x spread PF >= 1.15 and
# 100+ trades, profitable in more than half the quarters, the last 4 quarters profitable together at 2x, and an average R above every placebo seed.
# Added 2026-10-28 after a blind-spot review, before the run: financing on multi-day holds; and a second control - the PLAIN 20-day-average rule (long
# while zd > 0, short while zd < 0, same stop and machinery). Shuffled bars have no trend at all, so any trend rule beats them; the trend zones must also
# beat the plain moving-average rule's average R at 2x, or the experts add nothing. Per-pair and long/short totals are printed (concentration check).
MA_RULE = ((0.001, 99.0), (-99.0, -0.001))
def quarters(first: date, today: date) -> list[tuple[date, date]]:
    out, y, q = [], first.year, (first.month - 1) // 3
    while True:
        a = date(y, 3 * q + 1, 1)
        if a > today:
            break
        nq, ny = (q + 1) % 4, y + (q == 3)
        b = min(date(ny, 3 * nq + 1, 1) - timedelta(days=1), today)
        out.append((a, b))
        y, q = ny, nq
    return out


# ------------------------------------------------------------------ trend experts: buy / sell / TAKE-PROFIT zones, 3-week test (owner 2026-10-28)
# PRE-DECLARED before any run. Daily yardstick zd as above. From the 12 months before the test, for the TREND experts only (20+ signals or trades):
# BUY ZONE = their average median zd at long signals; SELL ZONE = at short signals; LONG TP ZONE = their average median zd at the EXIT of their
# WINNING long trades (each expert's own backtest, fxbot.engine.simulate); SHORT TP ZONE = the same for winning shorts; each +- 0.25.
# Trade: long when a 1-hour close is in the buy zone -> take profit when zd reaches the long TP zone's lower edge, get out early if zd falls back into the
# sell zone, hard stop 1.5 daily ATR, 20 days at most. Short mirrors it (TP at the short TP zone's upper edge, out early back in the buy zone).
# Test: the last 21 days, 7 majors, 1-hour bid/ask, normal and 2x spread, financing charged, 3 shuffled-bar seeds as the control.
def tp_zd(make, bars: pd.DataFrame, zd: pd.Series, a: date, b: date) -> tuple[list, list]:
    """zd at the exit of this expert's WINNING long / short trades whose entry is in [a, b]."""
    from .engine import simulate
    longs, shorts = [], []
    for t in simulate("x", bars, make()):
        if not (a <= t.entry_time.date() <= b) or t.side * (t.exit_px - t.entry_px) <= 0:
            continue
        pos = zd.index.searchsorted(t.exit_time, side="right") - 1
        if pos >= 0 and np.isfinite(zd.iloc[pos]):
            (longs if t.side == 1 else shorts).append(float(zd.iloc[pos]))
    return longs, shorts


def trend_tp_zones(sig: dict, tps: dict) -> dict:
    """sig: name -> (long zd list, short zd list); tps: name -> (winning-long exit zd, winning-short exit zd). Trend experts only."""
    def avg(lists):
        meds = [float(np.median(x)) for x in lists if len(x) >= MIN_SIGNALS]
        return ((np.mean(meds) - HALF_WIDTH, np.mean(meds) + HALF_WIDTH), len(meds)) if meds else (None, 0)
    names = [n for n in sig if n in TRENDING]
    return {"buy": avg([sig[n][0] for n in names]), "sell": avg([sig[n][1] for n in names]),
            "tp_long": avg([tps[n][0] for n in names if n in tps]), "tp_short": avg([tps[n][1] for n in names if n in tps])}


def trade_trend_tp(df: pd.DataFrame, bz, sz, tpl, tps, a: date, b: date, pair: str = "") -> list[dict]:
    from .engine import FIN_PER_NIGHT, _nights
    m = mid(df)
    zd, datr = zscore_daily(m)
    z, at = zd.to_numpy(float), datr.to_numpy(float)
    c = m["close"].to_numpy(float)
    bo, bh, bl, ao, ah, al = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    dates = np.asarray(df.index.date)
    out, i, n = [], 0, len(df)
    while i < n - 1:
        if not (a <= dates[i] <= b) or not np.isfinite(z[i]) or not np.isfinite(at[i]):
            i += 1
            continue
        side = 1 if bz[0] <= z[i] <= bz[1] else -1 if sz[0] <= z[i] <= sz[1] else 0
        if not side:
            i += 1
            continue
        j = i + 1
        fill = ao[j] if side == 1 else bo[j]
        sd = DAILY_STOP_ATR * at[i]
        stop = fill - side * sd
        k, px, why = j, None, ""
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            tp_hit = np.isfinite(z[k]) and ((side == 1 and z[k] >= tpl[0]) or (side == -1 and z[k] <= tps[1]))
            bail = np.isfinite(z[k]) and ((side == 1 and z[k] <= sz[1]) or (side == -1 and z[k] >= bz[0]))
            if k > j and side * (op - stop) <= 0:
                px, why = op, "stop"
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px, why = stop, "stop"
            elif tp_hit or bail or k - j + 1 >= DAILY_MAX_BARS:
                why = "tp" if tp_hit else "sell zone" if bail else "time"
                if k + 1 < n:
                    k += 1
                    px = bo[k] if side == 1 else ao[k]
                else:
                    px = c[k]
            elif k == n - 1:
                px, why = c[k], "end"
            if px is not None:
                break
            k += 1
        kk = min(k, n - 1)
        nts = _nights(df.index[j], df.index[kk])
        out.append({"date": dates[j], "side": side, "r": (side * (px - fill) - nts * FIN_PER_NIGHT * fill) / sd, "pair": pair, "why": why, "nights": nts})
        i = max(k, i + 1)
    return out
