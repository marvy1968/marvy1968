"""Stair-Step Trend Pipeline, research only (owner 2026-11-01). Follows /opt/marv-bot/state/stair_step_trend_SPEC.md (v1); tested next to the
Trend-Angle Pacer (alpacabot.anglepacer) with the same protocol and pass bar. Choices the spec leaves open are written here BEFORE any run.

UNIVERSE  No point-in-time S&P 500 list exists on the VM. MAIN = the bot's 55 (top 5 by market cap per sector, picked 2026-10-14):
          SURVIVORSHIP-BIASED (inflates long PF, deflates short PF). CROSS-CHECK = the 21-name 2016-largest control list (picked without
          hindsight, still not point-in-time). XLC members before XLC data exists (2018-06) count as XLK (GOOGL, META, TMUS, T, VZ) or XLY
          (NFLX, DIS); XLC is left out of the sector ranking until it has data.
DATA      Alpaca daily bars (split + dividend adjusted), 2016-06 -> now; weekly bars W-FRI, a week is used from its Friday close on.
INDICATORS Wilder ATR14, SMA20, BB(20,2) (population sd), Stoch(14,3,3) (%K = SMA3 of raw, %D = SMA3 of %K), Wilder RSI14, pacer strength
          (N=20 OLS slope / ATR x sqrt(R2)) daily and weekly. Swing low = 5-bar fractal (low below the 2 before, not above the 2 after),
          known 2 bars after the pivot. Anchored VWAP from the latest confirmed swing low (long) / high (short); alt weekly VWAP (Monday reset).
STAGE     weekly close vs MA30w, slope = MA30w - MA30w 4 weeks ago.
SECTORS   RS = ETF lookback return - SPY's, ranked at each week's last close, used the next week; long = top N, short = bottom N.
WATCHLIST rebuilt at each week's last close: per chosen sector, stage-eligible, price > $10, 20d $vol > $20M, weekly strength same sign
          and >= 0.05, 20d/100d volume >= 0.9, ATR/close 1-6%, daily strength on the side's sign; top 3 by |strength|.
ENTRY A   at close t: (1) low <= SMA20 + 0.25 ATR and close > SMA20 - 0.5 ATR; (2) %K crossed above %D in the last 3 bars and min %D of the
          last 5 < 30. Short mirror (> 70). Market entry at the next open.
     A2   A + close > VWAP + RSI14 40-55 (short 45-60) + "in the S/R buy zone" = a confirmed swing low within 40 bars and close <= it + 1.5 ATR.
     B    A entries, B exits.  B-full = A entries + close > VWAP, B exits.
STOP      candidates: last swing low within 40 bars - 0.25 ATR, SMA20 - 1 ATR (ATR and levels of the signal close, distance from the actual
          open). Nearer one if it is >= 1 ATR away, else the farther; floor 1 ATR; skip if > 3 ATR. R = entry - stop.
EXITS A   1/3 at +1R (then stop to entry + round-trip cost), 1/3 at +1.5R; runner trail from then on, ratcheted at each close: max(trail,
          latest higher swing low - 0.1 ATR, SMA20 - 0.25 ATR); TP signal (RSI >= 70, close >= 1.08 x SMA20, close >= upper BB) tightens the
          trail to that bar's low. Trend-break: 3 closes in a row below VWAP and SMA20 -> all out at the next open. 60 bars max (exit at close).
EXITS B   1/4 at 0.5R, 0.75R, 1R, 1.5R; after 0.5R stop -> -0.5R, after 1R -> entry + cost, then under each higher swing low - 0.1 ATR.
          Full exit at the next open on a TP signal (%K >= 70 or RSI >= 70 or close >= 1.08 x SMA20 or >= upper BB), a %K cross below %D
          from %D > 70, or the trend-break. 60 bars max.
BARS      stop first for the whole remaining size when a bar touches the stop and any target; targets fill in order otherwise; gaps at the
          open. Stop moves made by a target fill apply from the next bar.
PORTFOLIO one position per symbol, max 8 open, max 5 per side, max 2 per sector, 0.75% risk (0.5 / 1.0 shown), gross exposure 150% (a new
          trade is scaled down to fit, skipped below 10% of its size). Ties: highest |strength| first. A slot frees the day after the exit.
COSTS     12 bps a side (10 + 2 slippage), short borrow 0.5%/yr; 2x run doubles both.
PARAMS    swept on train only: RS lookback {42,63,126} x N {2,3} x VWAP {anchored, weekly}; best train PF with 30+ train trades, frozen.
CONTROLS  (holdout, frozen params, 20 seeds each, trades judged one by one): random-date = same symbol, month and side, random entry day,
          same stop rule and exits; shuffled = real entry day, random side, stop rule for that side, same exits.
CHECK     50 holdout trades of A (both sides, seed 0 sample) replayed on hourly bars (same rules, intraday order from the 1H bars).
ABLATION  variant A, both sides, frozen params: entry triggers on every stock / + stage / + stage + sector / + full watchlist (= A).
PASS      per variant and side: holdout PF > 1.1, 100+ holdout trades, train PF > 1.0 with a positive return, beats random 16+/20 and
          shuffled 16+/20, PF > 1.0 at 2x costs. PF on per-trade R (all partials of a trade combined).
"""

from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from .anglepacer import NY, _ns, ols, stock_daily, stock_weekly, htf_map, wilder_atr, stock_hourly

CS = 0.0012
RISK = 0.0075
LOOKS, TOPN, VWS = (42, 63, 126), (2, 3), ("anchored", "weekly")
VARIANTS = ("A", "A2", "B", "Bfull")
TRAIN0, HOLD0 = pd.Timestamp("2018-01-01"), pd.Timestamp("2024-01-01")
PRE_XLC = {"GOOGL": "XLK", "META": "XLK", "TMUS": "XLK", "T": "XLK", "VZ": "XLK", "NFLX": "XLY", "DIS": "XLY"}


# ---------------------------------------------------------------- indicators (closed bars only)
def _sma(x, n):
    return pd.Series(x).rolling(n).mean().to_numpy()


def _pivots(lo, hi):
    """Confirmed 5-bar fractals: at bar t the latest swing low / high confirmed by t (pivot p is known at p+2), its index and the one before."""
    n = len(lo)
    out = {k: np.full(n, np.nan) for k in ("sl", "sl_i", "sl_prev", "sh", "sh_i", "sh_prev")}
    sl = sli = slp = sh = shi = shp = np.nan
    for t in range(n):
        p = t - 2
        if p >= 2:
            w = lo[p - 2:p + 3]
            if np.all(np.isfinite(w)) and lo[p] < lo[p - 1] and lo[p] < lo[p - 2] and lo[p] <= lo[p + 1] and lo[p] <= lo[p + 2]:
                slp, sl, sli = sl, lo[p], p
            w = hi[p - 2:p + 3]
            if np.all(np.isfinite(w)) and hi[p] > hi[p - 1] and hi[p] > hi[p - 2] and hi[p] >= hi[p + 1] and hi[p] >= hi[p + 2]:
                shp, sh, shi = sh, hi[p], p
        out["sl"][t], out["sl_i"][t], out["sl_prev"][t] = sl, sli, slp
        out["sh"][t], out["sh_i"][t], out["sh_prev"][t] = sh, shi, shp
    return out


def _anchored_vwap(h, l, c, v, anchor_idx):
    typ = (h + l + c) / 3
    pv, vv = np.nan_to_num(typ * v), np.nan_to_num(v)
    cpv, cv = np.r_[0.0, np.cumsum(pv)], np.r_[0.0, np.cumsum(vv)]
    out = np.full(len(c), np.nan)
    ok = np.isfinite(anchor_idx)
    a = anchor_idx[ok].astype(int)
    t = np.flatnonzero(ok)
    den = cv[t + 1] - cv[a]
    out[t] = np.where(den > 0, (cpv[t + 1] - cpv[a]) / np.where(den > 0, den, 1), np.nan)
    return out


def _weekly_vwap(h, l, c, v, week):
    typ = (h + l + c) / 3
    df = pd.DataFrame({"pv": np.nan_to_num(typ * v), "v": np.nan_to_num(v), "w": week})
    g = df.groupby("w")
    den = g["v"].cumsum().to_numpy()
    return np.where(den > 0, g["pv"].cumsum().to_numpy() / np.where(den > 0, den, 1), np.nan)


def prep(dd: pd.DataFrame, ww: pd.DataFrame) -> dict:
    o, h, l, c, v = (dd[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    A = {"o": o, "h": h, "l": l, "c": c, "v": v, "dates": dd.index, "tc": dd["tclose"].to_numpy(np.int64), "n": len(c)}
    A["atr"] = wilder_atr(h, l, c)
    A["sma"] = _sma(c, 20)
    sd = pd.Series(c).rolling(20).std(ddof=0).to_numpy()
    A["bbu"], A["bbl"] = A["sma"] + 2 * sd, A["sma"] - 2 * sd
    ll, hh = pd.Series(l).rolling(14).min().to_numpy(), pd.Series(h).rolling(14).max().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = 100 * (c - ll) / np.where(hh - ll > 0, hh - ll, np.nan)
    A["k"] = _sma(raw, 3)
    A["d"] = _sma(A["k"], 3)
    dlt = np.r_[np.nan, np.diff(c)]
    g = pd.Series(np.where(dlt > 0, dlt, 0.0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()
    ls = pd.Series(np.where(dlt < 0, -dlt, 0.0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        A["rsi"] = np.where(ls > 0, 100 - 100 / (1 + g / np.where(ls > 0, ls, 1)), 100.0)
    slope, r2, _ = ols(c, 20)
    with np.errstate(invalid="ignore"):
        A["str"] = slope / A["atr"] * np.sqrt(r2)
    wc, wh, wl = (ww[k].to_numpy(float) for k in ("close", "high", "low"))
    ma = _sma(wc, 30)
    msl = ma - np.r_[np.full(4, np.nan), ma[:-4]]
    with np.errstate(invalid="ignore"):
        stage = np.where((wc > ma) & (msl > 0), 1.0, np.where((wc < ma) & (msl < 0), -1.0, 0.0))
        stage[~np.isfinite(msl)] = 0.0
        ws, wr2, _ = ols(wc, 20)
        wstr = ws / wilder_atr(wh, wl, wc) * np.sqrt(wr2)
    wtc = ww["tclose"].to_numpy(np.int64)
    A["stage"] = np.nan_to_num(htf_map(A["tc"], wtc, stage))
    A["wstr"] = htf_map(A["tc"], wtc, wstr)
    with np.errstate(invalid="ignore", divide="ignore"):
        A["volr"] = _sma(v, 20) / _sma(v, 100)
        A["atrp"] = A["atr"] / c
    A["dvol"] = _sma(c * v, 20)
    A.update(_pivots(l, h))
    A["vw_anchored_1"] = _anchored_vwap(h, l, c, v, A["sl_i"])
    A["vw_anchored_-1"] = _anchored_vwap(h, l, c, v, A["sh_i"])
    week = pd.DatetimeIndex(dd.index).to_period("W-FRI").astype(str).to_numpy()
    A["week"] = week
    A["vw_weekly_1"] = A["vw_weekly_-1"] = _weekly_vwap(h, l, c, v, week)
    return A


def align(A: dict, cal: pd.DatetimeIndex) -> dict:
    """Same arrays on the common trading calendar (NaN where the symbol has no bar); pivot indices are moved to calendar positions."""
    m = cal.get_indexer(pd.DatetimeIndex(A["dates"]))
    ok = m >= 0
    B = {"n": len(cal), "dates": cal}
    for k, x in A.items():
        if not isinstance(x, np.ndarray) or len(x) != A["n"] or x.dtype.kind not in "fi":
            continue
        y = np.full(len(cal), np.nan)
        if k in ("sl_i", "sh_i"):
            xi = x[ok]
            fin = np.isfinite(xi)
            z = np.full(len(xi), np.nan)
            z[fin] = m[xi[fin].astype(int)]
            z[z < 0] = np.nan
            y[m[ok]] = z
        else:
            y[m[ok]] = x[ok]
        B[k] = y
    B["stage"] = np.nan_to_num(B["stage"])
    return B


# ---------------------------------------------------------------- signals
def _tp_signal(A, k, side, with_stoch):
    c, sma = A["c"][k], A["sma"][k]
    if side == 1:
        x = A["rsi"][k] >= 70 or c >= 1.08 * sma or c >= A["bbu"][k]
        return bool(x or (with_stoch and A["k"][k] >= 70))
    x = A["rsi"][k] <= 30 or c <= 0.92 * sma or c <= A["bbl"][k]
    return bool(x or (with_stoch and A["k"][k] <= 30))


def _stoch_turn(A, k, side):
    K, D = A["k"], A["d"]
    if k < 1:
        return False
    if side == 1:
        return bool(K[k] < D[k] and K[k - 1] >= D[k - 1] and D[k - 1] > 70)
    return bool(K[k] > D[k] and K[k - 1] <= D[k - 1] and D[k - 1] < 30)


def entry_signal(A, t, side, var, vw) -> bool:
    if t < 6:
        return False
    c, l, h, sma, atr = A["c"][t], A["l"][t], A["h"][t], A["sma"][t], A["atr"][t]
    if not (np.isfinite(sma) and atr > 0):
        return False
    if side == 1:
        t1 = l <= sma + 0.25 * atr and c > sma - 0.5 * atr
    else:
        t1 = h >= sma - 0.25 * atr and c < sma + 0.5 * atr
    if not t1:
        return False
    K, D = A["k"], A["d"]
    cross = any((K[b] > D[b] and K[b - 1] <= D[b - 1]) if side == 1 else (K[b] < D[b] and K[b - 1] >= D[b - 1]) for b in (t - 2, t - 1, t))
    ext = np.nanmin(D[t - 4:t + 1]) < 30 if side == 1 else np.nanmax(D[t - 4:t + 1]) > 70
    if not (cross and ext):
        return False
    if var in ("A2", "Bfull"):
        v = vw[t]
        if not np.isfinite(v) or side * (c - v) <= 0:
            return False
    if var == "A2":
        r = A["rsi"][t]
        if not ((40 <= r <= 55) if side == 1 else (45 <= r <= 60)):
            return False
        lvl, li = (A["sl"][t], A["sl_i"][t]) if side == 1 else (A["sh"][t], A["sh_i"][t])
        if not np.isfinite(lvl) or t - li > 40 or side * (c - lvl) > 1.5 * atr:
            return False
    return True


def stop_for(A, t, side, entry) -> float | None:
    """Stop from the levels of the signal close t, distances from the actual entry."""
    atr, sma = A["atr"][t], A["sma"][t]
    cands = []
    lvl, li = (A["sl"][t], A["sl_i"][t]) if side == 1 else (A["sh"][t], A["sh_i"][t])
    if np.isfinite(lvl) and np.isfinite(li) and t - li <= 40:
        cands.append(lvl - side * 0.25 * atr)
    cands.append(sma - side * atr)
    ds = sorted(d for d in (side * (entry - x) for x in cands) if np.isfinite(d) and d > 0)
    if not ds:
        d = atr
    elif ds[0] >= atr:
        d = ds[0]
    else:
        d = max(ds[-1], atr)
    if not np.isfinite(d) or d > 3 * atr:
        return None
    return entry - side * d


# ---------------------------------------------------------------- one trade, in time order
def manage(A, j, side, entry, stop, var, mult, vw, bars_of=None, max_hold=60):
    """Walks one trade from its entry open (bar j). bars_of(k) -> intraday (o, h, l) bars of day k for the hourly check, else the daily bar.
    Returns (R combined over all partials, exit bar, reason, targets filled)."""
    n = A["n"]
    cs = CS * mult
    R0 = side * (entry - stop)
    if not (R0 > 0):
        return None
    ent = entry * (1 + side * cs)
    be = entry * (1 + side * 2 * cs)
    plan = [(1.0, 1 / 3), (1.5, 1 / 3)] if var in ("A", "A2") else [(0.5, .25), (0.75, .25), (1.0, .25), (1.5, .25)]
    tps = [entry + side * m * R0 for m, _ in plan]
    filled = [False] * len(plan)
    better = (lambda a, b: max(a, b)) if side == 1 else (lambda a, b: min(a, b))
    st = {"rem": 1.0, "R": 0.0}

    def fill(px, frac):
        st["R"] += frac * side * (px * (1 - side * cs) - ent) / R0
        st["rem"] -= frac

    why, pend, below, trail, state = None, None, 0, None, "stop"
    last = min(n - 1, j + max_hold - 1)
    k = j
    for k in range(j, last + 1):
        if pend is not None:
            fill(A["o"][k], st["rem"])
            why = pend
            break
        bars = bars_of(k) if bars_of is not None else None
        if not bars:
            bars = [(A["o"][k], A["h"][k], A["l"][k])]
        done = False
        for bo, bh, bl in bars:
            if not np.isfinite(bh):
                continue
            if side * (bo - stop) <= 0:
                fill(bo, st["rem"])
                why, done = state, True
            elif (bl <= stop) if side == 1 else (bh >= stop):
                fill(stop, st["rem"])
                why, done = state, True
            else:
                new_stop = stop
                for q, (m, frac) in enumerate(plan):
                    if filled[q]:
                        continue
                    if not ((bh >= tps[q]) if side == 1 else (bl <= tps[q])):
                        break
                    fill(bo if side * (bo - tps[q]) >= 0 else tps[q], frac)
                    filled[q] = True
                    if var in ("A", "A2") and q == 0:
                        new_stop, state = better(new_stop, be), "breakeven"
                    if var in ("B", "Bfull") and q == 0:
                        new_stop = better(new_stop, entry - side * 0.5 * R0)
                    if var in ("B", "Bfull") and q == 2:
                        new_stop, state = better(new_stop, be), "breakeven"
                stop = new_stop
                if st["rem"] <= 1e-9:
                    why, done = f"tp{len(plan)}", True
            if done:
                break
        if done:
            break
        if k - j + 1 >= max_hold:
            fill(A["c"][k], st["rem"])
            why = "time"
            break
        c, sma, atr = A["c"][k], A["sma"][k], A["atr"][k]
        v = vw[k]
        below = below + 1 if (np.isfinite(v) and side * (c - v) < 0 and side * (c - sma) < 0) else 0
        if below >= 3:
            pend = "trendbreak"
        hsl = A["sl"][k] if side == 1 else A["sh"][k]
        prev = A["sl_prev"][k] if side == 1 else A["sh_prev"][k]
        stair = hsl - side * 0.1 * atr if (np.isfinite(hsl) and np.isfinite(prev) and side * (hsl - prev) > 0) else np.nan
        if var in ("A", "A2"):
            if filled[1]:
                cands = [x for x in (stair, sma - side * 0.25 * atr) if np.isfinite(x)]
                for x in cands:
                    trail = x if trail is None else better(trail, x)
                if _tp_signal(A, k, side, False):
                    x = A["l"][k] if side == 1 else A["h"][k]
                    trail = x if trail is None else better(trail, x)
                if trail is not None:
                    stop, state = better(stop, trail), "trail"
        else:
            if _tp_signal(A, k, side, True) and pend is None:
                pend = "tpsignal"
            if _stoch_turn(A, k, side) and pend is None:
                pend = "stochdown"
            if filled[2] and np.isfinite(stair):
                stop, state = better(stop, stair), "trail"
    else:
        if st["rem"] > 1e-9:
            fill(A["c"][last], st["rem"])
            why = "end"
    if why is None:
        why = "end"
        if st["rem"] > 1e-9:
            fill(A["c"][k], st["rem"])
    R = st["R"]
    if side == -1:
        R -= mult * 0.005 * entry * (k - j + 1) * 1.4 / 365 / R0          # ~1.4 calendar days per trading day
    return R, k, why, sum(filled)


# ---------------------------------------------------------------- watchlists and the portfolio event loop
def sector_ranks(etf: dict, spy: np.ndarray, t: int, look: int) -> list[str]:
    rs = {}
    for e, x in etf.items():
        if t - look >= 0 and np.isfinite(x[t]) and np.isfinite(x[t - look]) and np.isfinite(spy[t - look]):
            rs[e] = x[t] / x[t - look] - spy[t] / spy[t - look]
    return sorted(rs, key=rs.get, reverse=True)


def watchlist(U, sect_of, ranks, t, side, topn, mode):
    chosen = set(ranks[:topn] if side == 1 else ranks[-topn:]) if ranks else set()
    by = {}
    for s, A in U.items():
        c = A["c"][t]
        if not np.isfinite(c) or not np.isfinite(A["str"][t]):
            continue
        if mode != "none" and A["stage"][t] != side:
            continue
        sec = sect_of(s, t)
        if mode in ("stage_sector", "full") and sec not in chosen:
            continue
        if mode == "full":
            ws = A["wstr"][t]
            if not (c > 10 and A["dvol"][t] > 20e6 and np.isfinite(ws) and side * ws >= 0.05 and A["volr"][t] >= 0.9
                    and 0.01 <= A["atrp"][t] <= 0.06 and side * A["str"][t] > 0):
                continue
        by.setdefault(sec, []).append((abs(A["str"][t]), s))
    out = {}
    for sec, xs in by.items():
        keep = sorted(xs, reverse=True)[:3] if mode == "full" else xs
        for sc, s in keep:
            out[s] = sc
    return out


def simulate(U, etf, spy, cal, sect_of, var, sides, look, topn, vwt, mult=1.0, mode="full", risk=RISK):
    n = len(cal)
    weeks = pd.DatetimeIndex(cal).to_period("W-FRI")
    wend = np.r_[weeks[1:] != weeks[:-1], True]
    VW = {s: {sd: A[f"vw_{vwt}_{sd}"] for sd in (1, -1)} for s, A in U.items()}
    wl = {1: {}, -1: {}}
    pos, queue, trades = [], [], []
    for t in range(n):
        pos = [p for p in pos if p["k"] >= t]
        if queue:
            for sc, s, side in sorted(queue, reverse=True):
                A = U[s]
                if any(p["s"] == s for p in pos) or len(pos) >= 8 or sum(p["side"] == side for p in pos) >= 5:
                    continue
                sec = sect_of(s, t)
                if sum(p["sec"] == sec for p in pos) >= 2:
                    continue
                e = A["o"][t]
                if not np.isfinite(e):
                    continue
                stop = stop_for(A, t - 1, side, e)
                if stop is None:
                    continue
                R0 = side * (e - stop)
                gross = risk * e / R0
                room = 1.5 - sum(p["gross"] for p in pos)
                scale = min(1.0, room / gross) if gross > 0 else 0
                if scale < 0.1:
                    continue
                res = manage(A, t, side, e, stop, var, mult, VW[s][side])
                if res is None:
                    continue
                R, k, why, nt = res
                p = {"s": s, "side": side, "sec": sec, "k": k, "gross": gross * scale}
                pos.append(p)
                trades.append({"sym": s, "side": side, "sec": sec, "j": t, "k": k, "t_in": cal[t], "t_out": cal[k], "R": R, "why": why,
                               "tps": nt, "risk": risk * scale, "entry": e, "stop": stop, "bps": R * R0 / e * 1e4})
            queue = []
        if wend[t]:
            ranks = sector_ranks(etf, spy, t, look)
            for side in sides:
                wl[side] = watchlist(U, sect_of, ranks, t, side, topn, mode)
        if t + 1 >= n:
            continue
        held = {p["s"] for p in pos if p["k"] > t}
        for side in sides:
            for s, sc in wl[side].items():
                if s not in held and entry_signal(U[s], t, side, var, VW[s][side]):
                    queue.append((sc, s, side))
    return trades


# ---------------------------------------------------------------- statistics
def pf(rs) -> float:
    x = np.asarray(list(rs), float)
    lo = -x[x < 0].sum()
    return float(x[x > 0].sum() / lo) if lo > 0 else (99.0 if len(x) and x.sum() > 0 else 0.0)


def eq_stats(trades, risk=None):
    eq, peak, dd = 1.0, 1.0, 0.0
    for t in sorted(trades, key=lambda t: t["t_out"]):
        eq *= 1 + (risk if risk is not None else t["risk"]) * t["R"]
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return 100 * (eq - 1), 100 * dd


def win(trades, a, b, by_exit=False):
    return [t for t in trades if a <= t["t_in"] < b and (not by_exit or t["t_out"] < b)]


def controls(U, trades, var, vwt, seeds=20, mult=1.0):
    rnd, shf = [], []
    month = {s: pd.DatetimeIndex(A["dates"]).year * 12 + pd.DatetimeIndex(A["dates"]).month for s, A in U.items()}
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        r1, r2 = [], []
        for t in trades:
            A, s = U[t["sym"]], t["sym"]
            m = month[s]
            days = np.flatnonzero((m == m[t["j"]]) & np.isfinite(A["o"]) & np.isfinite(A["atr"]))
            days = days[days >= 1]
            if len(days):
                j = int(rng.choice(days))
                st = stop_for(A, j - 1, t["side"], A["o"][j])
                if st is not None:
                    x = manage(A, j, t["side"], A["o"][j], st, var, mult, A[f"vw_{vwt}_{t['side']}"])
                    if x:
                        r1.append(x[0])
            side = int(rng.choice([1, -1]))
            j = t["j"]
            st = stop_for(A, j - 1, side, A["o"][j])
            if st is not None:
                x = manage(A, j, side, A["o"][j], st, var, mult, A[f"vw_{vwt}_{side}"])
                if x:
                    r2.append(x[0])
        rnd.append(pf(r1))
        shf.append(pf(r2))
    return rnd, shf


def evaluate(U, etf, spy, cal, sect_of, var, side_set, today, seeds=20, log=print):
    sides = {"long": (1,), "short": (-1,), "both": (1, -1)}[side_set]
    end = pd.Timestamp(today)
    runs = {}
    for look, topn, vwt in product(LOOKS, TOPN, VWS):
        runs[(look, topn, vwt)] = simulate(U, etf, spy, cal, sect_of, var, sides, look, topn, vwt)

    def pick(a, b):
        best, bp = None, -1.0
        for cb, tr in runs.items():
            w = win(tr, a, b, True)
            if len(w) >= 30 and pf(t["R"] for t in w) > bp:
                best, bp = cb, pf(t["R"] for t in w)
        return best

    cb = pick(TRAIN0, HOLD0)
    if cb is None:
        n_best = max(len(win(tr, TRAIN0, HOLD0, True)) for tr in runs.values())
        log(f"  {var} {side_set}: no combo had 30 train trades (max {n_best})")
        return {"var": var, "side": side_set, "error": f"no combo with 30+ train trades (max {n_best})"}
    tr = runs[cb]
    train, hold = win(tr, TRAIN0, HOLD0, True), win(tr, HOLD0, end + pd.Timedelta(days=1))
    two = win(simulate(U, etf, spy, cal, sect_of, var, sides, *cb, mult=2.0), HOLD0, end + pd.Timedelta(days=1))
    rnd, shf = controls(U, hold, var, cb[2], seeds)
    hp = pf(t["R"] for t in hold)
    wf, ts = [], pd.Timestamp("2021-01-01")
    while ts < end:
        te = ts + pd.DateOffset(months=6)
        c2 = pick(ts - pd.DateOffset(years=3), ts)
        if c2 is not None:
            wf += win(runs[c2], ts, te)
        ts = te
    y25 = win(tr, pd.Timestamp("2025-01-01"), pd.Timestamp("2026-01-01"))
    l6 = win(tr, end - pd.Timedelta(days=183), end + pd.Timedelta(days=1))
    tret, _ = eq_stats(train)
    hret, hdd = eq_stats(hold)
    r = {"var": var, "side": side_set, "combo": cb, "train_pf": pf(t["R"] for t in train), "train_ret": tret, "train_n": len(train),
         "hold_pf": hp, "hold_ret": hret, "hold_dd": hdd, "hold_n": len(hold), "win": float(np.mean([t["R"] > 0 for t in hold])) if hold else 0.0,
         "avg_r": float(np.mean([t["R"] for t in hold])) if hold else 0.0, "avg_bps": float(np.mean([t["bps"] for t in hold])) if hold else 0.0,
         "pf2": pf(t["R"] for t in two), "rand": sum(hp > x for x in rnd), "shuf": sum(hp > x for x in shf), "rand_med": float(np.median(rnd)),
         "shuf_med": float(np.median(shf)), "pf25": pf(t["R"] for t in y25), "n25": len(y25), "pf6": pf(t["R"] for t in l6), "n6": len(l6),
         "wf": pf(t["R"] for t in wf), "wf_n": len(wf), "trades": tr, "hold": hold,
         "years": {y: (pf(t["R"] for t in win(tr, pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 1}-01-01"))),
                       len(win(tr, pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 1}-01-01")))) for y in range(2018, end.year + 1)},
         "why": pd.Series([t["why"] for t in hold]).value_counts().to_dict() if hold else {},
         "risk_sweep": {rk: eq_stats(hold, rk)[0] for rk in (0.005, 0.0075, 0.01)}}
    r["pass"] = (hp > 1.1 and r["hold_n"] >= 100 and r["train_pf"] > 1.0 and tret > 0 and r["rand"] >= 16 and r["shuf"] >= 16 and r["pf2"] > 1.0)
    log(f"  {var} {side_set}: {cb} train PF {r['train_pf']:.2f} ({len(train)}), holdout PF {hp:.2f} ({len(hold)})")
    return r


def row(r, label=""):
    if "error" in r:
        return f"| {label}{r['var']} | {r['side']} | - | {r['error']} |" + " |" * 14
    look, topn, vwt = r["combo"]
    return (f"| {label}{r['var']} | {r['side']} | RS {look}d top/bottom {topn}, VWAP {vwt} | {r['train_pf']:.2f} | {r['train_ret']:+.1f} | "
            f"{r['hold_pf']:.2f} | {r['hold_ret']:+.1f} | {r['win']:.0%} | {r['avg_r']:+.2f} ({r['avg_bps']:+.0f} bps) | {r['hold_dd']:.1f} | "
            f"{r['hold_n']} | {r['pf2']:.2f} | {r['rand']}/20 | {r['shuf']}/20 | {r['pf25']:.2f} ({r['n25']}) | {r['pf6']:.2f} ({r['n6']}) | "
            f"{r['wf']:.2f} ({r['wf_n']}) | {'PASS' if r['pass'] else 'FAIL'} |")


# ---------------------------------------------------------------- entry point
def run(stock_load, today: date, out_dir: Path, log=print) -> list[str]:
    from .universe import CONTROL_SECTOR_OF, SECTOR_ETFS, SECTOR_OF
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = {}
    for s in sorted(set(SECTOR_OF) | set(CONTROL_SECTOR_OF) | set(SECTOR_ETFS) | {"SPY"}):
        try:
            d = stock_daily(stock_load(s, "1Day", date(2016, 6, 1), today))
            raw[s] = d[d.index < pd.Timestamp(today)]
        except Exception as exc:
            log(f"skip {s}: {exc!r}")
    cal = raw["SPY"].index
    spy = raw["SPY"]["close"].reindex(cal).to_numpy(float)
    etf = {e: raw[e]["close"].reindex(cal).to_numpy(float) for e in SECTOR_ETFS if e in raw}
    xlc_start = raw["XLC"].index.min() if "XLC" in raw else pd.Timestamp("2018-06-19")

    def universe(names):
        U = {}
        for s in names:
            if s in raw and len(raw[s]) > 200:
                U[s] = align(prep(raw[s], stock_weekly(raw[s])), cal)
        return U

    def sect_fn(table):
        def f(s, t):
            sec = table.get(s)
            if sec == "XLC" and cal[t] < xlc_start + pd.Timedelta(days=150):   # XLC needs ~6 months of bars before its RS can be ranked
                return PRE_XLC.get(s, "XLK")
            return sec
        return f

    main, ctrl = universe(SECTOR_OF), universe(CONTROL_SECTOR_OF)
    sm, sc = sect_fn(SECTOR_OF), sect_fn(CONTROL_SECTOR_OF)
    log(f"universe: main {len(main)} stocks, control {len(ctrl)}, sector ETFs {len(etf)}")
    res = {}
    for var in VARIANTS:
        for sd in ("long", "short", "both"):
            res[(var, sd)] = evaluate(main, etf, spy, cal, sm, var, sd, today, log=log)
    cres = {}
    for var in ("A", "B"):
        for sd in ("long", "both"):
            cres[(var, sd)] = evaluate(ctrl, etf, spy, cal, sc, var, sd, today, log=log)

    hdr = ("| Variant | Side | Params | Train PF | Train Ret% | Holdout PF | Holdout Ret% | Win% | Avg R | MaxDD% | Holdout trades | PF @2x | "
           "Random /20 | Shuffled /20 | 2025 PF (n) | Last 6m PF (n) | WF OOS PF (n) | PASS/FAIL |")
    out = [f"## Stair-Step Trend Pipeline (spec v1, /opt/marv-bot/state/stair_step_trend_SPEC.md) - train 2018-2023, holdout 2024-01-01..{today}",
           "**SURVIVORSHIP-BIASED**: no point-in-time S&P 500 list on the VM; main universe = today's 55 largest (5 per sector). "
           "Cross-check on the 2016-largest control list below.", "", "### Main universe (55, survivorship-biased)", hdr, "|" + "---|" * 18]
    out += [row(r) for r in res.values()]
    out += ["", "### Cross-check: 2016-largest control list (21 names, chosen without hindsight, not point-in-time)", hdr, "|" + "---|" * 18]
    out += [row(r, "ctrl ") for r in cres.values()]
    out.append("")
    out.append("Trend-Angle Pacer (2026-11-01, same rules): stocks-daily holdout PF 0.70 (386 t), stocks-1H 0.55, forex-H1 0.60 - all FAIL.")
    out.append("")
    for var in VARIANTS:
        r = res[(var, "both")]
        if "error" in r:
            continue
        out.append(f"- {var} both, per year: " + ", ".join(f"{y} {p:.2f} ({n})" for y, (p, n) in r["years"].items()))
        out.append(f"  holdout exits: {r['why']} | random median PF {r['rand_med']:.2f}, shuffled median {r['shuf_med']:.2f} | avg trade "
                   f"{r['avg_bps']:+.0f} bps vs 24 bps round-trip cost | holdout Ret% at 0.5/0.75/1.0% risk: "
                   + "/".join(f"{v:+.1f}" for v in r["risk_sweep"].values()))
    ra = res[("A", "both")]
    if "error" not in ra:
        secs = {}
        for t in ra["hold"]:
            secs.setdefault(t["sec"], []).append(t["R"])
        out.append("- A both, holdout per sector: " + ", ".join(f"{k} PF {pf(v):.2f} ({len(v)} t, {sum(v):+.1f}R)" for k, v in sorted(secs.items())))
        look, topn, vwt = ra["combo"]
        out.append("- ablation (A exits, both sides, frozen params; holdout PF / trades | train PF):")
        for mode, label in (("none", "entry triggers only, every stock"), ("stage", "+ weekly stage"), ("stage_sector", "+ sector RS"),
                            ("full", "+ watchlist gates / top 3 (= A)")):
            tr = simulate(main, etf, spy, cal, sm, "A", (1, -1), look, topn, vwt, mode=mode)
            h, trn = win(tr, HOLD0, pd.Timestamp(today) + pd.Timedelta(days=1)), win(tr, TRAIN0, HOLD0, True)
            out.append(f"  {label}: {pf(t['R'] for t in h):.2f} / {len(h)} | {pf(t['R'] for t in trn):.2f} ({len(trn)})")
        sample = ra["hold"]
        if len(sample) > 50:
            idx = np.random.default_rng(0).choice(len(sample), 50, replace=False)
            sample = [sample[i] for i in sorted(idx)]
        d_r, h_r = [], []
        hcache = {}
        for t in sample:
            s = t["sym"]
            if s not in hcache:
                try:
                    hh = stock_hourly(stock_load(s, "5Min", date(2023, 12, 1), today))
                    day = pd.to_datetime(hh["topen"].to_numpy(), utc=True).tz_convert(NY).normalize().tz_localize(None)
                    hcache[s] = {d: list(zip(g["open"], g["high"], g["low"])) for d, g in hh.groupby(day)}
                except Exception as exc:
                    log(f"hourly {s}: {exc!r}")
                    hcache[s] = {}
            A = main[s]
            bars_of = lambda k, A=A, s=s: hcache[s].get(A["dates"][k])
            x = manage(A, t["j"], t["side"], t["entry"], t["stop"], "A", 1.0, A[f"vw_{vwt}_{t['side']}"], bars_of=bars_of)
            if x:
                d_r.append(t["R"])
                h_r.append(x[0])
        if d_r:
            out.append(f"- HOURLY CHECK (A both, {len(d_r)} holdout trades replayed on 1H bars): PF {pf(d_r):.2f} on daily bars vs {pf(h_r):.2f} on "
                       f"hourly, mean R {np.mean(d_r):+.2f} vs {np.mean(h_r):+.2f}"
                       + (" -> daily INFLATED" if pf(d_r) - pf(h_r) > 0.2 else " -> daily result holds"))
    for (var, sd), r in res.items():
        if "trades" in r:
            pd.DataFrame(r["trades"]).to_csv(out_dir / f"trades_{var}_{sd}.csv", index=False)
    (out_dir / "report.md").write_text("\n".join(out) + "\n")
    return out
