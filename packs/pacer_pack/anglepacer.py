"""Trend-Angle Pacer, research only (owner 2026-11-01). Follows /opt/marv-bot/state/trend_angle_pacer_SPEC.md (v1) exactly; where the spec
leaves a detail open the choice is written here BEFORE any run. Log-only research: nothing here places orders or touches a service.

MARKETS  stocks-1H (RTH hourly bars built from 5-min SIP, buckets 9:30-10:30 ... 15:30-16:00; filter = daily bars), stocks-daily (filter =
         weekly), forex-H1 (OANDA bid/ask, filter = H4 built from H1, buckets aligned to 17:00 New York). Stocks = the bot's fixed 55 + SPY,
         QQQ (list fixed in 2026: survivorship bias, reported as a caveat). Data 2018-01 -> now.
ANGLE    Wilder ATR(14); OLS slope of close over N bars / ATR = angle; R2 of that fit; strength = angle x sqrt(R2); accel = angle - angle 5
         bars ago. The filter timeframe uses the same N and only its last CLOSED bar (nominal close time <= the base bar's close time).
DIRECTION LONG: strength >= thr, filter strength >= thr, R2 >= r2, accel > -0.02. SHORT mirror. 27 combos thr {.03,.05,.08} x N {14,20,30}
         x R2 {.25,.35,.5}; chosen by train PF (train = trades entered and exited 2018-01-01..2023-12-31, at least 30 of them), frozen.
ZONES    at the close of bar t, while flat: long limit at L - 0.25 ATR (L = fitted line value at t), stop L - 2 ATR (zone bottom - 1 ATR),
         TP = limit + angle x ATR x 10; skipped unless TP distance >= 1.5 ATR and >= 6 x round-trip cost. A new zone replaces the pending
         order; orders expire after 5 bars. Fill when the bar's low reaches the limit, at the limit (never the better open). Fill bar: stop
         checked, TP not (order unknown). Later bars: stop first, then TP; gaps fill at the open. Exit at the close of the 20th bar held, or
         at the next open after the base direction flips to the opposite side. One position per symbol; max 5 stocks / 4 forex at once.
         Equity: 0.5% risk per trade, compounded at each exit.
COSTS    stocks 0.03% a side (0.02% slippage + 0.01% half-spread; SPY/QQQ 0.01%), short borrow 0.5%/yr. Forex: the bar's real half-spread
         + 0.3 pip a side, +1 pip in the 17:00-18:00 ET hour. 2x run doubles all of it.
CONTROLS (holdout, frozen params, 20 seeds each) random-time: for every real trade a random bar of the same symbol, month and side, market
         entry at that bar's open, the same stop / TP distances in ATR, same 20-bar time stop (no flip exit). Shuffled-direction: the real
         entry bar and price, random side, same distances. Pacer beats a control run when its PF is higher.
ZONE HIT every zone placed in the holdout (thinned to one per symbol per 5 bars because consecutive zones overlap): hit = TP before stop
         within 20 bars, stop first, filled or not. Versus chance stop/(stop+TP) (one-sided binomial) and random-time zones with the same
         geometry in ATR (two-proportion z).
WALK-FWD 3-year train / 6-month test from 2021-01, step 6 months, re-picked each window, stitched.
HOURLY CHECK every stocks-daily holdout trade replayed on the hourly bars (fill hour, then stop / TP in hourly order); daily PF more than 0.2
         above the replayed PF = the daily result is inflated.
PASS     (per market) holdout PF > 1.1, 100+ holdout trades, train PF > 1.0 with a positive return, beats random-time 16+/20 and shuffled
         16+/20, PF > 1.0 at 2x costs.
"""

from datetime import date, timedelta
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

NY = "America/New_York"
THR, NS, R2S = (0.03, 0.05, 0.08), (14, 20, 30), (0.25, 0.35, 0.5)
ACC, K, H, HOLD, TP_MIN, COST_X = 0.02, 5, 10, 20, 1.5, 6.0
RISK = 0.005
TRAIN0, HOLD0 = pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")
NS_PER_DAY = 86400e9


# ---------------------------------------------------------------- indicators (past bars only)
def wilder_atr(h, l, c, n=14) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def ols(c: np.ndarray, n: int):
    """Rolling OLS of close on 0..n-1: slope, R2 and the fitted value at the last bar (direct window sums, no future values)."""
    m = len(c)
    out = [np.full(m, np.nan) for _ in range(3)]
    if m < n:
        return out
    sy = np.convolve(c, np.ones(n), "valid")
    syy = np.convolve(c * c, np.ones(n), "valid")
    sxy = np.convolve(c, np.arange(n - 1, -1, -1, dtype=float), "valid")   # sum_k k * c[t-n+1+k]
    mx, vx = (n - 1) / 2, (n * n - 1) / 12
    my = sy / n
    cov = sxy / n - mx * my
    vy = np.maximum(syy / n - my * my, 1e-18)
    slope = cov / vx
    r2 = np.clip(cov * cov / (vx * vy), 0, 1)
    fit = my + slope * mx
    for arr, v in zip(out, (slope, r2, fit)):
        arr[n - 1:] = v
    return out


def features(o, h, l, c, n):
    atr = wilder_atr(h, l, c)
    slope, r2, fit = ols(c, n)
    ang = slope / atr
    acc = ang - np.r_[np.full(5, np.nan), ang[:-5]]
    return {"atr": atr, "angle": ang, "r2": r2, "fit": fit, "str": ang * np.sqrt(r2), "acc": acc}


def htf_map(base_close_ns: np.ndarray, htf_close_ns: np.ndarray, htf_val: np.ndarray) -> np.ndarray:
    """Value of the last higher-timeframe bar whose (nominal) close is at or before each base bar's close."""
    j = np.searchsorted(htf_close_ns, base_close_ns, side="right") - 1
    out = np.full(len(base_close_ns), np.nan)
    ok = j >= 0
    out[ok] = htf_val[j[ok]]
    return out


# ---------------------------------------------------------------- bars
def _ns(idx) -> np.ndarray:
    return pd.DatetimeIndex(idx).tz_convert("UTC").as_unit("ns").asi8.astype(np.int64)


def stock_daily(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    idx = pd.DatetimeIndex(d.index)
    d.index = idx.tz_convert(NY).tz_localize(None).normalize() if idx.tz is not None else idx.normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d["tclose"] = _ns((d.index + pd.Timedelta(hours=16)).tz_localize(NY))
    d["topen"] = _ns((d.index + pd.Timedelta(hours=9, minutes=30)).tz_localize(NY))
    return d


def stock_weekly(dd: pd.DataFrame) -> pd.DataFrame:
    w = dd[["open", "high", "low", "close"]].resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    w["tclose"] = _ns((w.index + pd.Timedelta(hours=16)).tz_localize(NY))     # nominal Friday 16:00: a short week is used a day late
    return w


def stock_hourly(m5: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(m5.index)
    idx = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize(NY)
    mins = idx.hour * 60 + idx.minute - 570
    keep = (mins >= 0) & (mins < 390)
    m5, idx, mins = m5[keep], idx[keep], mins[keep]
    start = idx.normalize() + pd.to_timedelta(570 + (mins // 60) * 60, unit="m")
    g = m5.groupby(start)
    hh = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
    hh.index = pd.DatetimeIndex(hh.index)
    end = hh.index + pd.Timedelta(minutes=60)
    end = end.where(~((hh.index.hour == 15) & (hh.index.minute == 30)), hh.index + pd.Timedelta(minutes=30))
    hh["tclose"] = _ns(end)
    hh["topen"] = _ns(hh.index)
    return hh


def fx_h1(raw: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame({k: (raw["b" + k[0]] + raw["a" + k[0]]) / 2 for k in ("open", "high", "low", "close")}, index=raw.index)
    f["hs"] = (raw["ao"] - raw["bo"]) / 2
    idx = pd.DatetimeIndex(f.index)
    idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    f.index = idx
    f = f[~f.index.duplicated(keep="last")].sort_index()
    f["topen"] = _ns(f.index)
    f["tclose"] = f["topen"] + np.int64(3600 * 10 ** 9)
    f["roll"] = (f.index.tz_convert(NY).hour == 17).astype(float)
    return f


def fx_h4(h1: pd.DataFrame) -> pd.DataFrame:
    loc = h1[["open", "high", "low", "close"]].copy()
    loc.index = loc.index.tz_convert(NY)
    w = loc.resample("4h", offset="1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    w["tclose"] = _ns(w.index + pd.Timedelta(hours=4))
    return w


# ---------------------------------------------------------------- market container
def build(base: dict, htf: dict, kind: str, cost: dict | None = None) -> dict:
    """base / htf: symbol -> DataFrame with open/high/low/close/tclose(/topen, hs, roll). Precomputes features for every N."""
    syms = sorted(s for s in base if s in htf and len(base[s]) > 60)
    S = []
    for s in syms:
        b, hf = base[s], htf[s]
        o, h, l, c = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        tc = b["tclose"].to_numpy(np.int64)
        topen = b["topen"].to_numpy(np.int64) if "topen" in b else tc
        A = {"sym": s, "o": o, "h": h, "l": l, "c": c, "tc": tc, "to": topen, "n": len(o)}
        months = pd.DatetimeIndex(pd.to_datetime(topen, utc=True)).tz_convert(NY if kind == "stock" else "UTC")
        A["month"] = (months.year * 12 + months.month).to_numpy()
        if kind == "stock":
            A["cfrac"] = (cost or {}).get(s, 0.0003)
            A["cabs"] = np.zeros(len(o))
        else:
            pip = 0.01 if s.endswith("JPY") else 0.0001
            A["cfrac"] = 0.0
            A["cabs"] = b["hs"].to_numpy(float) + 0.3 * pip + b["roll"].to_numpy(float) * pip
        A["feat"] = {}
        ho, hh_, hl, hc = (hf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        htc = hf["tclose"].to_numpy(np.int64)
        for n in NS:
            f = features(o, h, l, c, n)
            fh = features(ho, hh_, hl, hc, n)
            f["hstr"] = htf_map(tc, htc, fh["str"])
            A["feat"][n] = f
        S.append(A)
    tc_all = np.concatenate([A["tc"] for A in S])
    s_all = np.concatenate([np.full(A["n"], k) for k, A in enumerate(S)])
    i_all = np.concatenate([np.arange(A["n"]) for A in S])
    order = np.lexsort((s_all, tc_all))
    return {"kind": kind, "S": S, "ev_s": s_all[order].tolist(), "ev_i": i_all[order].tolist(),
            "maxpos": 5 if kind == "stock" else 4}


def direction(A: dict, thr: float, n: int, r2: float) -> np.ndarray:
    f = A["feat"][n]
    with np.errstate(invalid="ignore"):
        lg = (f["str"] >= thr) & (f["hstr"] >= thr) & (f["r2"] >= r2) & (f["acc"] > -ACC)
        sh = (f["str"] <= -thr) & (f["hstr"] <= -thr) & (f["r2"] >= r2) & (f["acc"] < ACC)
    return np.where(lg, 1, np.where(sh, -1, 0)).astype(np.int8)


# ---------------------------------------------------------------- strict time-order event loop
def simulate(mk: dict, combo: tuple, mult: float = 1.0, long_only: bool = False, zones: bool = False):
    thr, n, r2 = combo
    S = mk["S"]
    D = [direction(A, thr, n, r2).tolist() for A in S]
    L = [A["feat"][n]["fit"].tolist() for A in S]
    ANG = [A["feat"][n]["angle"].tolist() for A in S]
    ATR = [A["feat"][n]["atr"].tolist() for A in S]
    O, Hh, Ll, C = ([A[k].tolist() for A in S] for k in ("o", "h", "l", "c"))
    CA = [A["cabs"].tolist() for A in S]
    CF = [A["cfrac"] for A in S]
    TC = [A["tc"].tolist() for A in S]
    stock = mk["kind"] == "stock"
    pos, order = [None] * len(S), [None] * len(S)
    nopen, trades, zrec = 0, [], []
    maxpos = mk["maxpos"]

    def cost(s, i, px):
        return mult * (CF[s] * px + CA[s][i])

    def close(s, i, px, why):
        nonlocal nopen
        p = pos[s]
        side = p["side"]
        ent = p["limit"] + side * p["ce"]
        ex = px - side * cost(s, i, px)
        R = side * (ex - ent) / p["risk"]
        if stock and side == -1:
            R -= mult * 0.005 * p["limit"] * (TC[s][i] - TC[s][p["i"]]) / NS_PER_DAY / 365 / p["risk"]
        trades.append({"s": s, "sym": S[s]["sym"], "side": side, "i": p["i"], "j": i, "t_in": TC[s][p["i"]], "t_out": TC[s][i], "R": R,
                       "why": why, "limit": p["limit"], "stop": p["stop"], "tp": p["tp"], "atr": p["atr"], "oi": p["oi"]})
        pos[s] = None
        nopen -= 1

    for s, i in zip(mk["ev_s"], mk["ev_i"]):
        o, h, l, c = O[s][i], Hh[s][i], Ll[s][i], C[s][i]
        p = pos[s]
        if p is not None:
            side = p["side"]
            if p["flip"]:
                close(s, i, o, "flip")
            elif side * (o - p["stop"]) <= 0:
                close(s, i, o, "stop")
            elif (l <= p["stop"]) if side == 1 else (h >= p["stop"]):
                close(s, i, p["stop"], "stop")
            elif side * (o - p["tp"]) >= 0:
                close(s, i, o, "tp")
            elif (h >= p["tp"]) if side == 1 else (l <= p["tp"]):
                close(s, i, p["tp"], "tp")
            elif i - p["i"] + 1 >= HOLD:
                close(s, i, c, "time")
        elif order[s] is not None:
            od = order[s]
            if i > od["exp"]:
                order[s] = None
            elif ((l <= od["limit"]) if od["side"] == 1 else (h >= od["limit"])) and nopen < maxpos:
                order[s] = None
                pos[s] = dict(od, i=i, flip=False, ce=cost(s, i, od["limit"]))
                nopen += 1
                if (l <= od["stop"]) if od["side"] == 1 else (h >= od["stop"]):
                    close(s, i, od["stop"], "stop")
        # ---- bar i closed: new direction / zone
        d = D[s][i]
        if long_only and d == -1:
            d = 0
        p = pos[s]
        if p is not None:
            if d == -p["side"]:
                p["flip"] = True
            continue
        if d == 0:
            continue
        atr, fit, ang = ATR[s][i], L[s][i], ANG[s][i]
        if not (atr > 0) or fit != fit or ang != ang:
            continue
        limit = fit - d * 0.25 * atr
        stop = fit - d * 2.0 * atr
        tpd = abs(ang) * atr * H
        rt = 2 * cost(s, i, c)
        if tpd < TP_MIN * atr or tpd < COST_X * rt:
            continue
        od = {"side": d, "limit": limit, "stop": stop, "tp": limit + d * tpd, "risk": abs(limit - stop), "atr": atr, "exp": i + K, "oi": i}
        order[s] = od
        if zones:
            zrec.append((s, i, d, limit, stop, limit + d * tpd, c, atr))
    return trades, zrec


# ---------------------------------------------------------------- statistics
def pf(rs) -> float:
    x = np.asarray(rs, float)
    lo = -x[x < 0].sum()
    return float(x[x > 0].sum() / lo) if lo > 0 else (99.0 if len(x) and x.sum() > 0 else 0.0)


def equity(trades) -> tuple[float, float]:
    eq, peak, dd = 1.0, 1.0, 0.0
    for t in sorted(trades, key=lambda t: t["t_out"]):
        eq *= 1 + RISK * t["R"]
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return 100 * (eq - 1), 100 * dd


def window(trades, a, b, by_exit=False):
    a, b = a.value, b.value
    return [t for t in trades if a <= t["t_in"] < b and (not by_exit or t["t_out"] < b)]


def walk(A, j, side, entry, stop, tp, mult, n_hold=HOLD):
    """Control walk from entry bar j: fill bar checks the stop only; then stop first, TP, gaps at the open; time stop at bar j+19."""
    o, h, l, c, ca, cf, tc = A["o"], A["h"], A["l"], A["c"], A["cabs"], A["cfrac"], A["tc"]
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    ent = entry + side * mult * (cf * entry + ca[j])
    last = min(A["n"], j + n_hold) - 1
    px, k = None, last
    for k in range(j, last + 1):
        if k > j and side * (o[k] - stop) <= 0:
            px = o[k]
        elif (l[k] <= stop) if side == 1 else (h[k] >= stop):
            px = stop
        elif k > j and side * (o[k] - tp) >= 0:
            px = o[k]
        elif k > j and ((h[k] >= tp) if side == 1 else (l[k] <= tp)):
            px = tp
        if px is not None:
            break
    if px is None:
        px, k = c[last], last
    R = side * (px - side * mult * (cf * px + ca[k]) - ent) / risk
    if A["cfrac"] and side == -1:
        R -= mult * 0.005 * entry * (tc[k] - tc[j]) / NS_PER_DAY / 365 / risk
    return R


def _month_bars(A):
    if "_mb" not in A:
        atr = A["feat"][NS[0]]["atr"]
        ok = np.flatnonzero(np.isfinite(np.r_[np.nan, atr[:-1]]))
        ok = ok[ok < A["n"] - 1]
        mb = {}
        for m, i in zip(A["month"][ok], ok):
            mb.setdefault(int(m), []).append(int(i))
        A["_mb"] = {m: np.asarray(v) for m, v in mb.items()}
    return A["_mb"]


def controls(mk, trades, mult, seeds=20):
    """Random-time and shuffled-direction control PFs (one per seed)."""
    S = mk["S"]
    rnd, shf = [], []
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        r1, r2 = [], []
        for t in trades:
            A = S[t["s"]]
            stop_k, tp_k = abs(t["limit"] - t["stop"]) / t["atr"], abs(t["tp"] - t["limit"]) / t["atr"]
            bars = _month_bars(A).get(int(A["month"][t["i"]]))
            if bars is not None and len(bars):
                j = int(rng.choice(bars))
                atr = A["feat"][NS[0]]["atr"][j - 1]
                e = A["o"][j]
                x = walk(A, j, t["side"], e, e - t["side"] * stop_k * atr, e + t["side"] * tp_k * atr, mult)
                if x is not None:
                    r1.append(x)
            side = int(rng.choice([1, -1]))
            e = t["limit"]
            x = walk(A, t["i"], side, e, e - side * abs(t["limit"] - t["stop"]), e + side * abs(t["tp"] - t["limit"]), mult)
            if x is not None:
                r2.append(x)
        rnd.append(pf(r1))
        shf.append(pf(r2))
    return rnd, shf


def zone_hits(mk, zrec, a, seeds=1):
    from scipy.stats import binomtest, norm
    S = mk["S"]
    last, keep = {}, []
    for z in zrec:
        s, i = z[0], z[1]
        if S[s]["tc"][i] < a.value or i - last.get(s, -10 ** 9) < 5:
            continue
        last[s] = i
        keep.append(z)

    def hit(A, i, side, stop, tp):
        h, l = A["h"], A["l"]
        for k in range(i + 1, min(A["n"], i + 1 + HOLD)):
            if (l[k] <= stop) if side == 1 else (h[k] >= stop):
                return 0
            if (h[k] >= tp) if side == 1 else (l[k] <= tp):
                return 1
        return 0

    rng = np.random.default_rng(7)
    hits, ch, rh = [], [], []
    for s, i, side, limit, stop, tp, c, atr in keep:
        A = S[s]
        hits.append(hit(A, i, side, stop, tp))
        ch.append(abs(limit - stop) / (abs(limit - stop) + abs(tp - limit)))
        bars = _month_bars(A).get(int(A["month"][i]))
        if bars is not None and len(bars):
            j = int(rng.choice(bars)) - 1
            a2 = A["feat"][NS[0]]["atr"][j]
            if np.isfinite(a2) and a2 > 0:
                cj = A["c"][j]
                rh.append(hit(A, j, side, cj + (stop - c) / atr * a2, cj + (tp - c) / atr * a2))
    n = len(hits)
    if not n:
        return {"n": 0}
    k, p0 = int(sum(hits)), float(np.mean(ch))
    pb = binomtest(k, n, p0, alternative="greater").pvalue
    p1, p2, n2 = k / n, (np.mean(rh) if rh else 0.0), len(rh)
    pp = (k + sum(rh)) / (n + n2) if n2 else p1
    se = np.sqrt(pp * (1 - pp) * (1 / n + 1 / max(n2, 1))) if 0 < pp < 1 else 1.0
    pz = float(1 - norm.cdf((p1 - p2) / se))
    return {"n": n, "hit": p1, "chance": p0, "p": float(pb), "rand": p2, "p_rand": pz}


# ---------------------------------------------------------------- hourly replay of daily trades (3.3)
def hourly_replay(md, mh, trades, mult=1.0):
    hs = {A["sym"]: A for A in mh["S"]}
    out, nofill = [], 0
    for t in trades:
        A = hs.get(t["sym"])
        D = md["S"][t["s"]]
        if A is None:
            nofill += 1
            continue
        d_in, d_out = D["tc"][t["i"]], D["tc"][t["j"]]
        day0 = d_in - int(6.5 * 3600e9)
        a = int(np.searchsorted(A["tc"], day0, side="right"))
        b = int(np.searchsorted(A["tc"], d_out, side="right"))
        side, lim, stop, tp = t["side"], t["limit"], t["stop"], t["tp"]
        if t["why"] == "flip":
            b = int(np.searchsorted(A["tc"], d_out - int(6.5 * 3600e9), side="right"))
        o, h, l, c, cf = A["o"], A["h"], A["l"], A["c"], A["cfrac"]
        filled, px, kx = None, None, None
        for k in range(a, min(b, A["n"])):
            if filled is None:
                if A["tc"][k] > d_in:
                    break
                if (l[k] <= lim) if side == 1 else (h[k] >= lim):
                    filled = k
                    if (l[k] <= stop) if side == 1 else (h[k] >= stop):
                        px, kx = stop, k
                        break
                continue
            if side * (o[k] - stop) <= 0:
                px, kx = o[k], k
            elif (l[k] <= stop) if side == 1 else (h[k] >= stop):
                px, kx = stop, k
            elif side * (o[k] - tp) >= 0:
                px, kx = o[k], k
            elif (h[k] >= tp) if side == 1 else (l[k] <= tp):
                px, kx = tp, k
            if px is not None:
                break
        if filled is None:
            nofill += 1
            continue
        if px is None:                        # no hourly stop / TP: the daily exit stands (time close, flip open, or a feed mismatch)
            px = D["o"][t["j"]] if t["why"] == "flip" else (D["c"][t["j"]] if t["why"] == "time" else (stop if t["why"] == "stop" else tp))
        ent = lim + side * mult * cf * lim
        R = side * (px - side * mult * cf * px - ent) / abs(lim - stop)
        if side == -1:
            R -= mult * 0.005 * lim * (t["t_out"] - t["t_in"]) / NS_PER_DAY / 365 / abs(lim - stop)
        out.append(R)
    return out, nofill


# ---------------------------------------------------------------- one market
def evaluate(mk, name, today: date, seeds=20, log=print):
    combos = list(product(THR, NS, R2S))
    end = pd.Timestamp(today, tz="UTC")
    runs = {}
    for cb in combos:
        runs[cb], _ = simulate(mk, cb)
        log(f"  {name} {cb}: {len(runs[cb])} trades")
    def pick(a, b):
        best, bp = None, -1.0
        for cb, tr in runs.items():
            w = window(tr, a, b, by_exit=True)
            if len(w) >= 30 and pf([t["R"] for t in w]) > bp:
                best, bp = cb, pf([t["R"] for t in w])
        return best
    cb = pick(TRAIN0, HOLD0)
    if cb is None:
        return {"name": name, "error": "no combo had 30 train trades"}
    tr_all, zrec = simulate(mk, cb, zones=True)
    train = window(tr_all, TRAIN0, HOLD0, by_exit=True)
    hold = window(tr_all, HOLD0, end + pd.Timedelta(days=1))
    two, _ = simulate(mk, cb, mult=2.0)
    hold2 = window(two, HOLD0, end + pd.Timedelta(days=1))
    rnd, shf = controls(mk, hold, 1.0, seeds)
    hp = pf([t["R"] for t in hold])
    zh = zone_hits(mk, zrec, HOLD0)
    wf, ts = [], pd.Timestamp("2021-01-01", tz="UTC")
    while ts < end:
        te = ts + pd.DateOffset(months=6)
        c2 = pick(ts - pd.DateOffset(years=3), ts)
        if c2 is not None:
            wf += window(runs[c2], ts, te)
        ts = te
    y25 = window(tr_all, pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC"))
    l6 = window(tr_all, end - pd.Timedelta(days=183), end + pd.Timedelta(days=1))
    years = {y: pf([t["R"] for t in window(tr_all, pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC"))])
             for y in range(2018, end.year + 1)}
    ny = {y: len(window(tr_all, pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC"))) for y in years}
    tret, _ = equity(train)
    hret, hdd = equity(hold)
    res = {"name": name, "combo": cb, "train_pf": pf([t["R"] for t in train]), "train_ret": tret, "train_n": len(train),
           "hold_pf": hp, "hold_ret": hret, "hold_dd": hdd, "hold_n": len(hold), "win": float(np.mean([t["R"] > 0 for t in hold])) if hold else 0.0,
           "pf2": pf([t["R"] for t in hold2]), "rand_beat": sum(hp > x for x in rnd), "shuf_beat": sum(hp > x for x in shf),
           "rand_med": float(np.median(rnd)), "shuf_med": float(np.median(shf)), "zone": zh, "pf25": pf([t["R"] for t in y25]),
           "pf6m": pf([t["R"] for t in l6]), "n25": len(y25), "n6m": len(l6), "wf_pf": pf([t["R"] for t in wf]), "wf_n": len(wf),
           "years": years, "years_n": ny, "hold_trades": hold, "trades": tr_all,
           "why": {w: sum(t["why"] == w for t in hold) for w in ("tp", "stop", "time", "flip")},
           "long": (sum(t["side"] == 1 for t in hold), pf([t["R"] for t in hold if t["side"] == 1])),
           "short": (sum(t["side"] == -1 for t in hold), pf([t["R"] for t in hold if t["side"] == -1]))}
    res["pass"] = (res["hold_pf"] > 1.1 and res["hold_n"] >= 100 and res["train_pf"] > 1.0 and res["train_ret"] > 0 and
                   res["rand_beat"] >= 16 * seeds // 20 and res["shuf_beat"] >= 16 * seeds // 20 and res["pf2"] > 1.0)
    if mk["kind"] == "stock":
        lo, _ = simulate(mk, cb, long_only=True)
        lo_h = window(lo, HOLD0, end + pd.Timedelta(days=1))
        lo2, _ = simulate(mk, cb, mult=2.0, long_only=True)
        lr, ls = controls(mk, lo_h, 1.0, seeds)
        lp = pf([t["R"] for t in lo_h])
        lo_tr = window(lo, TRAIN0, HOLD0, by_exit=True)
        res["long_only"] = {"hold_pf": lp, "hold_n": len(lo_h), "hold_ret": equity(lo_h)[0], "hold_dd": equity(lo_h)[1],
                            "win": float(np.mean([t["R"] > 0 for t in lo_h])) if lo_h else 0.0, "train_pf": pf([t["R"] for t in lo_tr]),
                            "train_ret": equity(lo_tr)[0], "pf2": pf([t["R"] for t in window(lo2, HOLD0, end + pd.Timedelta(days=1))]),
                            "rand_beat": sum(lp > x for x in lr), "shuf_beat": sum(lp > x for x in ls)}
    return res


def row(r, label=None):
    if "error" in r:
        return f"| {r['name']} | - | {r['error']} |" + " |" * 15
    z = r["zone"]
    zs = f"{z['hit']:.0%} vs {z['chance']:.0%} (p={z['p']:.3f}); random zones {z['rand']:.0%} (p={z['p_rand']:.3f}), n={z['n']}" if z.get("n") else "-"
    thr, n, r2 = r["combo"]
    return (f"| {label or r['name']} | thr {thr}, N {n}, R2 {r2} | {r['train_pf']:.2f} | {r['train_ret']:+.1f} | {r['hold_pf']:.2f} | "
            f"{r['hold_ret']:+.1f} | {r['win']:.0%} | {r['hold_dd']:.1f} | {r['hold_n']} | {r['pf2']:.2f} | {r['rand_beat']}/20 | "
            f"{r['shuf_beat']}/20 | {zs} | {r['pf25']:.2f} ({r['n25']}) | {r['pf6m']:.2f} ({r['n6m']}) | {r['wf_pf']:.2f} ({r['wf_n']}) | "
            f"{'PASS' if r['pass'] else 'FAIL'} |")


def lo_row(r):
    lo = r.get("long_only")
    if not lo:
        return ""
    ok = lo["hold_pf"] > 1.1 and lo["hold_n"] >= 100 and lo["train_pf"] > 1.0 and lo["train_ret"] > 0 and lo["rand_beat"] >= 16 \
        and lo["shuf_beat"] >= 16 and lo["pf2"] > 1.0
    return (f"| {r['name']} LONG ONLY | same | {lo['train_pf']:.2f} | {lo['train_ret']:+.1f} | {lo['hold_pf']:.2f} | {lo['hold_ret']:+.1f} | "
            f"{lo['win']:.0%} | {lo['hold_dd']:.1f} | {lo['hold_n']} | {lo['pf2']:.2f} | {lo['rand_beat']}/20 | {lo['shuf_beat']}/20 | - | - | - | - | "
            f"{'PASS' if ok else 'FAIL'} |")


# ---------------------------------------------------------------- entry point (alpaca run-all --families anglepacer)
def run(stock_load, fx_load, symbols, today: date, out_dir: Path, log=print) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cost = {s: (0.0001 if s in ("SPY", "QQQ") else 0.0003) for s in symbols}
    res = []
    dd, hh = {}, {}
    for s in symbols:
        try:
            dd[s] = stock_daily(stock_load(s, "1Day", date(2017, 1, 1), today))
            hh[s] = stock_hourly(stock_load(s, "5Min", date(2017, 12, 1), today))
            log(f"loaded {s}: {len(dd[s])} daily, {len(hh[s])} hourly")
        except Exception as exc:
            log(f"skip {s}: {exc!r}")
    md = build(dd, {s: stock_weekly(d) for s, d in dd.items()}, "stock", cost)
    rd = evaluate(md, "stocks-daily (weekly filter)", today, log=log)
    mh = build(hh, {s: dd[s] for s in hh if s in dd}, "stock", cost)
    rh = evaluate(mh, "stocks-1H (daily filter)", today, log=log)
    res += [rd, rh]
    rep, nofill = hourly_replay(md, mh, rd.get("hold_trades", []))
    fx = {}
    try:
        from fxbot.data import MAJORS
        for s in MAJORS:
            fx[s] = fx_h1(fx_load(s, "H1", date(2017, 9, 1), today))
            log(f"loaded {s}: {len(fx[s])} H1")
    except Exception as exc:
        log(f"forex skipped: {exc!r}")
    rf = evaluate(build(fx, {s: fx_h4(d) for s, d in fx.items()}, "fx"), "forex-H1 (H4 filter)", today, log=log) if fx else None
    if rf:
        res.append(rf)

    out = [f"## Trend-Angle Pacer (spec v1, /opt/marv-bot/state/trend_angle_pacer_SPEC.md) - train 2018-2023, holdout 2024-01-01..{today}",
           "| Market/TF | Params | Train PF | Train Ret% | Holdout PF | Holdout Ret% | Win% | MaxDD% | Holdout trades | PF @2x cost | "
           "Random beats /20 | Shuffled beats /20 | Zone hit% vs chance (p) | 2025 PF (n) | Last 6m PF (n) | WF OOS PF (n) | PASS/FAIL |",
           "|" + "---|" * 17]
    for r in res:
        out.append(row(r))
        if r.get("long_only"):
            out.append(lo_row(r))
    out.append("")
    for r in res:
        if "error" in r:
            continue
        out.append(f"- {r['name']}: per-year PF " + ", ".join(f"{y} {v:.2f} ({r['years_n'][y]})" for y, v in r["years"].items())
                   + f" | holdout exits {r['why']} | long {r['long'][0]} t PF {r['long'][1]:.2f}, short {r['short'][0]} t PF {r['short'][1]:.2f}"
                   + f" | random median PF {r['rand_med']:.2f}, shuffled median PF {r['shuf_med']:.2f}")
    if "hold_pf" in rd:
        rp = pf(rep)
        out.append(f"- HOURLY CHECK: stocks-daily holdout PF {rd['hold_pf']:.2f} on daily bars vs {rp:.2f} replayed on 1H bars "
                   f"({len(rep)} trades, {nofill} with no hourly fill) -> "
                   + ("daily result INFLATED (daily > replay + 0.2)" if rd["hold_pf"] - rp > 0.2 else "daily result holds on hourly bars")
                   + (f"; stocks-1H strategy holdout PF {rh['hold_pf']:.2f}" if "hold_pf" in rh else ""))
    for r in res:
        if "trades" in r:
            pd.DataFrame([{k: v for k, v in t.items() if k != "s"} for t in r["trades"]]).to_csv(
                out_dir / f"trades_{r['name'].split()[0]}.csv", index=False)
    (out_dir / "report.md").write_text("\n".join(out) + "\n")
    return out
