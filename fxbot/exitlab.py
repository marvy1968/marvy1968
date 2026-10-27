"""Forex exit lab (owner 2026-10-27, "test all with each ... short test first, then a long one if it passes"): the stock exit lab
(alpacabot.exitlab) on every OANDA strategy with its own entries and initial stop distance held fixed. Bid/ask bars: longs fill at the ask and
exit at the bid, shorts the reverse, so every trade pays the real spread; a gap through a stop fills at the open; inside a bar the stop is checked
before the target; trailing stops move at the bar close and act from the next bar; every trade is closed after 500 bars at most. Results in R
(the initial stop distance). Financing is left out (small next to R on these holding times).

Exits: own (the strategy's exact backtest, engine.simulate), live (0.5R cut + 1.25R target: the live runner's rule on its bars; the live 10-minute
under-water exit cannot be seen on 1-hour bars), fixed_1r/2r/3r, time_10/20 (bars), atr_trail_2/3 (chandelier from the best price), sma20_close,
sma50_close, swing_5 (the last 5 bars' low / high), be1r_atr2, psar, own_atr3 (own exit flags + target plus a 3 ATR chandelier).

Short test: the last 3 months in two halves at the normal spread and at 2x. PASS = 3+ trades and PF >= 1.0 in each half at 2x, 10+ trades, total R above the
live exits. Long test (3 best passers per strategy + live + own): 5 years up to the short window, its last 12 months sequestered.
KEEP = OOS 40+ trades, PF >= 1.15 at 2x, beats the live exits, PF > 1 in most years, beats random entries with the same exit (5 seeds)."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from alpacabot.exitlab import _fmt, stats

from .data import mid
from .engine import simulate as engine_simulate

EXITS = ("own", "live", "fixed_1r", "fixed_2r", "fixed_3r", "time_10", "time_20", "atr_trail_2", "atr_trail_3", "sma20_close", "sma50_close",
         "swing_5", "be1r_atr2", "psar", "own_atr3")
MAX_BARS = 500


def _atr(m: pd.DataFrame, n: int = 14) -> np.ndarray:
    pc = m["close"].shift(1)
    tr = pd.concat([m["high"] - m["low"], (m["high"] - pc).abs(), (m["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().to_numpy(float)


def _spread(df: pd.DataFrame, mult: float) -> pd.DataFrame:
    if mult == 1.0:
        return df
    out = df.copy()
    for k in "ohlc":
        md, half = (df["b" + k] + df["a" + k]) / 2, (df["a" + k] - df["b" + k]) / 2 * mult
        out["b" + k], out["a" + k] = md - half, md + half
    return out


def simulate(df: pd.DataFrame, strat, exit_: str, start, end, spread_mult: float = 1.0, seed: int | None = None) -> list[dict]:
    """One instrument, one position at a time. Returns [{'entry': date, 'r': float}]."""
    if exit_ == "own" and seed is None:
        return [{"entry": t.entry_time.date(), "r": t.side * (t.exit_px - t.entry_px) / t.stop_dist}
                for t in engine_simulate("x", df, strat, spread_mult) if start <= t.entry_time.date() <= end]
    df = _spread(df, spread_mult)
    m = mid(df)
    f = strat.frame(m)
    n = len(df)
    bo, bh, bl, bc, ao, ah, al, ac = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"))
    mc = m["close"].to_numpy(float)
    lg, sh = f["long"].to_numpy(bool).copy(), f["short"].to_numpy(bool).copy()
    stop_d = f["stop"].to_numpy(float)
    if seed is not None:                                          # random entries: same count per side, same stop distances
        rng = np.random.default_rng(seed)
        ok = np.flatnonzero(np.isfinite(stop_d) & (stop_d > 0))
        newl, news = np.zeros(n, bool), np.zeros(n, bool)
        for arr, cnt in ((newl, int(lg.sum())), (news, int(sh.sum()))):
            if cnt and len(ok):
                arr[rng.choice(ok, size=min(cnt, len(ok)), replace=False)] = True
        lg, sh = newl, news
    xl, xs = f["exit_long"].to_numpy(bool), f["exit_short"].to_numpy(bool)
    tgt_d = f["target"].to_numpy(float) if "target" in f else np.full(n, np.nan)
    a = _atr(m)
    s20, s50 = (m["close"].rolling(k).mean().to_numpy(float) for k in (20, 50))
    dates = np.asarray(df.index.date)
    out, i = [], 0
    while i < n - 1:
        side = 1 if lg[i] else -1 if sh[i] else 0
        sd = float(stop_d[i])
        if side == 0 or not np.isfinite(sd) or sd <= 0 or not np.isfinite(a[i]) or not (start <= dates[i] <= end):
            i += 1
            continue
        j0 = i + 1
        fill = ao[j0] if side == 1 else bo[j0]
        stop = fill - side * sd
        own_t = tgt_d[i] if np.isfinite(tgt_d[i]) and tgt_d[i] > 0 else np.inf
        if exit_ == "live":
            stop, tdist = fill - side * 0.5 * sd, 1.25 * sd
        else:
            tdist = {"own_atr3": own_t, "fixed_1r": sd, "fixed_2r": 2 * sd, "fixed_3r": 3 * sd}.get(exit_, np.inf)
        if seed is not None and exit_ == "own_atr3":
            tdist = 1.25 * sd
        tgt = fill + side * tdist if np.isfinite(tdist) else None
        best, sar, ep, af = fill, stop, fill, 0.02
        k, px = j0, None
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            if k > j0 and side * (op - stop) <= 0:
                px = op
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px = stop
            elif tgt is not None and k > j0 and side * (op - tgt) >= 0:
                px = op
            elif tgt is not None and ((side == 1 and hi >= tgt) or (side == -1 and lo <= tgt)):
                px = tgt
            else:
                held = k - j0 + 1
                own_flag = (xl[k] if side == 1 else xs[k]) and seed is None and exit_ == "own_atr3"
                flag = own_flag or {"time_10": held >= 10, "time_20": held >= 20,
                                    "sma20_close": side * (mc[k] - s20[k]) < 0, "sma50_close": side * (mc[k] - s50[k]) < 0}.get(exit_, False)
                if flag or held >= MAX_BARS:
                    if k + 1 < n:
                        k += 1
                        px = bo[k] if side == 1 else ao[k]
                    else:
                        px = bc[k] if side == 1 else ac[k]
                elif k == n - 1:
                    px = bc[k] if side == 1 else ac[k]
            if px is not None:
                break
            best = max(best, hi) if side == 1 else min(best, lo)        # the trail moves at the close and acts from the next bar
            lvl = None
            if exit_ in ("atr_trail_2", "atr_trail_3", "own_atr3"):
                lvl = best - side * (2.0 if exit_ == "atr_trail_2" else 3.0) * a[i]
            elif exit_ == "swing_5":
                lvl = float(np.min(bl[max(j0, k - 4):k + 1])) if side == 1 else float(np.max(ah[max(j0, k - 4):k + 1]))
            elif exit_ == "be1r_atr2" and side * (best - fill) >= sd:
                lvl = fill if side * ((best - side * 2.0 * a[i]) - fill) < 0 else best - side * 2.0 * a[i]
            elif exit_ == "psar":
                if k > j0:
                    if side * (best - ep) > 0:
                        ep, af = best, min(af + 0.02, 0.2)
                    sar = sar + af * (ep - sar)
                lvl = min(sar, lo) if side == 1 else max(sar, hi)
            if lvl is not None and side * (lvl - stop) > 0:
                stop = lvl
            k += 1
        k = min(k, n - 1)
        out.append({"entry": dates[j0], "r": side * (px - fill) / sd})
        i = max(k, i + 1)
    return out


def run(make, data: dict, exit_: str, start, end, spread_mult: float = 1.0, seed: int | None = None) -> list[dict]:
    return [t for df in data.values() for t in simulate(df, make(), exit_, start, end, spread_mult, seed)]


def _sel(tr, a, b):
    return [t["r"] for t in tr if a <= t["entry"] <= b]


def short_test(strats: dict, data: dict, today: date, days: int = 92) -> dict:
    """strats: name -> (make, granularity); data: granularity -> {instrument: bid/ask bars}."""
    a = today - timedelta(days=days)
    mid_ = a + (today - a) / 2
    res = {}
    for name, (make, g) in strats.items():
        rows = {}
        for ex in EXITS:
            tr = run(make, data[g], ex, a, today)
            tr2 = run(make, data[g], ex, a, today, spread_mult=2.0)
            rows[ex] = {"all": stats([t["r"] for t in tr]), "all_2x": stats([t["r"] for t in tr2]),
                        "half1_2x": stats(_sel(tr2, a, mid_)), "half2_2x": stats(_sel(tr2, mid_ + timedelta(days=1), today))}
        live_r = rows["live"]["all_2x"]["total_r"]
        for ex, r in rows.items():
            r["pass"] = bool(ex != "live" and r["all_2x"]["trades"] >= 10 and r["all_2x"]["total_r"] > live_r
                             and all(r[k]["trades"] >= 3 and (r[k]["pf"] or 0) >= 1.0 for k in ("half1_2x", "half2_2x")))
        res[name] = rows
    return res


def best_passers(short: dict, k: int = 3) -> dict:
    return {st: [ex for ex, _ in sorted(((ex, r) for ex, r in rows.items() if r["pass"]), key=lambda x: -(x[1]["all_2x"]["pf"] or 0))[:k]]
            for st, rows in short.items()}


def long_test(strats: dict, data: dict, today: date, picks: dict, years: int = 5, seeds: int = 5) -> dict:
    end = today - timedelta(days=93)
    first, oos_a = end - timedelta(days=365 * years), end - timedelta(days=365)
    res = {}
    for name, exs in picks.items():
        if not exs:
            continue
        make, g = strats[name]
        rows = {}
        for ex in ["live", "own"] + [e for e in exs if e not in ("live", "own")]:
            tr = run(make, data[g], ex, first, end, spread_mult=2.0)
            by_year = {y: stats([t["r"] for t in tr if t["entry"].year == y]) for y in sorted({t["entry"].year for t in tr})}
            plc = [stats(_sel(run(make, data[g], ex, oos_a, end, 2.0, seed=s), oos_a, end))["avg_r"] for s in range(seeds)]
            plc = [p for p in plc if p is not None]
            rows[ex] = {"in_sample": stats(_sel(tr, first, oos_a - timedelta(days=1))), "oos": stats(_sel(tr, oos_a, end)),
                        "years_pf_gt_1": sum(1 for v in by_year.values() if (v["pf"] or 0) > 1), "years": len(by_year),
                        "by_year": {str(y): v for y, v in by_year.items()},
                        "random_entry_oos_avg_r": round(float(np.mean(plc)), 3) if plc else None}
        live_oos = rows["live"]["oos"]["total_r"]
        for ex, r in rows.items():
            o = r["oos"]
            r["keep"] = bool(ex != "live" and o["trades"] >= 40 and (o["pf"] or 0) >= 1.15 and o["total_r"] > live_oos
                             and r["years_pf_gt_1"] > r["years"] / 2
                             and (r["random_entry_oos_avg_r"] is None or (o["avg_r"] or -9) > r["random_entry_oos_avg_r"]))
        res[name] = rows
    return res


def short_lines(short: dict) -> list[str]:
    out = []
    for st, rows in short.items():
        out.append(f"### {st} (short test, last 3 months)")
        for ex, r in rows.items():
            out.append(f"- {ex}: normal spread {_fmt(r['all'])} | 2x spread {_fmt(r['all_2x'])} | halves at 2x PF {r['half1_2x']['pf']} "
                       f"({r['half1_2x']['trades']}) / {r['half2_2x']['pf']} ({r['half2_2x']['trades']}){' -> PASS' if r['pass'] else ''}")
    return out


def long_lines(long: dict) -> list[str]:
    out = []
    for st, rows in long.items():
        out.append(f"### {st} long test (2x spread, last 12 months sequestered)")
        for ex, r in rows.items():
            tag = " (control)" if ex == "live" else (" -> KEEP" if r["keep"] else " -> reject")
            out.append(f"- {ex}: in-sample {_fmt(r['in_sample'])} | OOS {_fmt(r['oos'])} | years PF>1 {r['years_pf_gt_1']}/{r['years']} | "
                       f"random-entry OOS avg R {r['random_entry_oos_avg_r']}{tag}")
            if ex in ("live", "own"):
                out.append("  by year: " + ", ".join(f"{y} PF {v['pf']} {v['total_r']:+}R ({v['trades']})" for y, v in r["by_year"].items()))
    return out
