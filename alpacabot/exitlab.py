"""Exit lab (owner 2026-10-27, "test all with each. Do a short test first, then a long one if it passes. For the best ones."):
every exit type from Gemini's inventory on every live swing strategy, with the strategy's own ENTRIES and initial STOPS held fixed.

Entries (daily bars, filled at the next open + slippage, one position per stock at a time, re-entry allowed after an exit):
  topdown   Sector Top-Down (strategies.SectorTopDown): own exit = close < EMA20, stop EMA20 - 1 ATR
  funnel    the live Stock Funnel rules (zones.json swing.funnel): own exit = sell vote / stage change, take-profit 1.25R
  oversold  the live oversold confluence (zones.json swing.oversold): own exit = close > 20-day average, take-profit 1.25R
            (live enters at 10:00; here at the open, so its row is a little optimistic or pessimistic, the same for every exit)
  index200  SPY / QQQ while the close is above the 200-day average: own exit = close below it, stop 4 ATR

Exits, all declared before any result (each keeps the initial stop; a gap through any stop fills at the open; inside a bar the stop is
checked before the target; trailing stops move at the close and act from the next bar; every trade is closed after 120 days at most):
  own, fixed_1r, fixed_2r, fixed_3r, time_10, time_20, atr_trail_2, atr_trail_3 (chandelier from the highest high), sma20_close,
  sma50_close, swing_low_5 (lowest low of the last 5 bars), be1r_atr2 (breakeven at +1R, then a 2 ATR trail), psar (parabolic 0.02 / 0.2),
  own_atr3 (own exits plus a 3 ATR chandelier).

Short test: the last 3 months in two halves at 2x slippage. PASS = profit factor >= 1.0 in both halves, 10+ trades, and total R above
the strategy's own exits. Long test (only for the 3 best passers per strategy and the own exits): 2016 to 3 months ago, the last
12 months of it sequestered, on today's list AND the hindsight-free 2016 list. KEEP = OOS 40+ trades, OOS PF >= 1.15 at 2x slippage,
beats own exits in OOS R, PF > 1 in most years, and beats random entries with the same exit (5 seeds).
Nothing here trades: it returns numbers, including bad ones."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .indicators import atr

EXITS = ("own", "fixed_1r", "fixed_2r", "fixed_3r", "time_10", "time_20", "atr_trail_2", "atr_trail_3", "sma20_close", "sma50_close",
         "swing_low_5", "be1r_atr2", "psar", "own_atr3")
STRATEGIES = ("topdown", "funnel", "oversold", "index200")
MAX_HOLD = 120
FUNNEL_RULES = {"stop_frac": 0.5, "tp_r": 1.25, "v_tail": True}
OVERSOLD_RULES = {"oversold": True, "os_stage": None, "tp_r": 1.25, "stop_frac": 0.75}


def signals(name: str, d: pd.DataFrame, spy_close: pd.Series | None = None) -> pd.DataFrame:
    """entry / stop (price) / tp (price, inf = none) / exit (own exit flag at the close) per bar."""
    c = d["close"].astype(float)
    out = pd.DataFrame(index=d.index)
    if name == "topdown":
        from .strategies import SectorTopDown
        f = SectorTopDown(spy_close).frame(d)
        out["entry"], out["stop"], out["tp"], out["exit"] = f["entry"], f["stop"], np.inf, f["exit"]
    elif name in ("funnel", "oversold"):
        from .funnel import frame
        f = frame(d, FUNNEL_RULES if name == "funnel" else OVERSOLD_RULES)
        out["entry"], out["stop"], out["tp"], out["exit"] = f["long_entry"], f["long_stop"], f["long_tp"], f["long_exit"]
    elif name == "index200":
        m, a = c.rolling(200).mean(), atr(d, 14)
        out["entry"], out["stop"], out["tp"], out["exit"] = (c > m) & a.notna(), c - 4 * a, np.inf, c < m
    else:
        raise ValueError(name)
    out["entry"] = out["entry"].fillna(False).astype(bool)
    out["exit"] = out["exit"].fillna(False).astype(bool)
    return out


def _psar_step(sar, ep, af, high):
    if high > ep:
        ep, af = high, min(af + 0.02, 0.2)
    return sar + af * (ep - sar), ep, af


def simulate(d: pd.DataFrame, sig: pd.DataFrame, exit_: str, start, end, slip_bps: float = 5.0, entry_mask=None) -> list[dict]:
    """One stock: every trade of `sig`'s entries managed by `exit_`. entry_mask (bool array) replaces the entries (random-entry control)."""
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(d, 14).to_numpy(float)
    s20, s50 = (d["close"].rolling(n).mean().to_numpy(float) for n in (20, 50))
    ent = sig["entry"].to_numpy(bool) if entry_mask is None else entry_mask
    stops, tps, own_x = sig["stop"].to_numpy(float), sig["tp"].to_numpy(float), sig["exit"].to_numpy(bool)
    dates, n, slip = np.asarray(d.index.date), len(d), slip_bps / 1e4
    out, i = [], 0
    while i < n - 1:
        if not ent[i] or not (start <= dates[i] <= end):
            i += 1
            continue
        fill = o[i + 1] * (1 + slip)
        stop0 = stops[i] if entry_mask is None else c[i] - 1.5 * a[i]
        risk = fill - stop0
        if not np.isfinite(risk) or risk <= 0 or not np.isfinite(a[i]):
            i += 1
            continue
        ai = a[i]
        tgt = {"own": tps[i], "own_atr3": tps[i], "fixed_1r": fill + risk, "fixed_2r": fill + 2 * risk, "fixed_3r": fill + 3 * risk}.get(exit_, np.inf)
        if entry_mask is not None and exit_ in ("own", "own_atr3"):
            tgt = fill + 1.25 * risk
        if not np.isfinite(tgt):
            tgt = np.inf
        if tgt <= fill:
            i += 1
            continue
        stop, hh, j, px, why = stop0, h[i + 1], i + 1, None, ""
        sar, ep, af = stop0, h[i + 1], 0.02
        while j < n:
            if j > i + 1 and o[j] <= stop:
                px, why = o[j], "stop"
            elif l[j] <= stop:
                px, why = min(stop, o[j]), "stop"
            elif h[j] >= tgt:
                px, why = max(tgt, o[j]), "target"
            if px is None:
                held = j - i
                flag = {"own": own_x[j] and entry_mask is None, "own_atr3": own_x[j] and entry_mask is None,
                        "time_10": held >= 10, "time_20": held >= 20,
                        "sma20_close": c[j] < s20[j], "sma50_close": c[j] < s50[j]}.get(exit_, False)
                if entry_mask is not None and exit_ in ("own", "own_atr3"):
                    flag = c[j] < s20[j]                                      # random entries have no own signal: use the 20-day close
                if flag or held >= MAX_HOLD:
                    if j + 1 < n:
                        j, px, why = j + 1, o[j + 1], ("signal" if flag else "max_hold")
                    else:
                        px, why = c[j], "end"
                elif j == n - 1:
                    px, why = c[j], "end"
            if px is not None:
                break
            hh = max(hh, h[j])                                                # trail updates at the close, acts from the next bar
            if exit_ in ("atr_trail_2", "atr_trail_3", "own_atr3"):
                stop = max(stop, hh - (2.0 if exit_ == "atr_trail_2" else 3.0) * ai)
            elif exit_ == "swing_low_5":
                stop = max(stop, float(np.min(l[max(i + 1, j - 4):j + 1])))
            elif exit_ == "be1r_atr2":
                if hh - fill >= risk:
                    stop = max(stop, fill, hh - 2.0 * ai)
            elif exit_ == "psar":
                if j > i + 1:
                    sar, ep, af = _psar_step(sar, ep, af, h[j])
                stop = max(stop, min(sar, l[j]))
            j += 1
        px *= (1 - slip)
        out.append({"entry_date": dates[i + 1], "exit_date": dates[min(j, n - 1)], "r": (px - fill) / risk, "reason": why,
                    "days": int(min(j, n - 1) - i)})
        i = j + 1 if why != "end" else n
    return out


def stats(rs: list[float], risk_pct: float = 0.005) -> dict:
    if not rs:
        return {"trades": 0, "pf": None, "win": None, "avg_r": None, "total_r": 0.0, "ret_pct": 0.0}
    gw, gl = sum(x for x in rs if x > 0), -sum(x for x in rs if x <= 0)
    return {"trades": len(rs), "pf": round(gw / gl, 2) if gl > 0 else 99.0, "win": round(float(np.mean([x > 0 for x in rs])), 3),
            "avg_r": round(float(np.mean(rs)), 3), "total_r": round(float(sum(rs)), 2), "ret_pct": round(100 * risk_pct * sum(rs), 2)}


def run(name: str, bars: dict, exit_: str, start, end, slip_bps: float = 5.0, spy_close=None, seed: int | None = None) -> list[dict]:
    trades = []
    rng = np.random.default_rng(seed) if seed is not None else None
    for sym, d in bars.items():
        if len(d) < 220 or (name == "index200") != (sym in ("SPY", "QQQ")):
            continue
        sig = signals(name, d, spy_close)
        mask = None
        if rng is not None:                                           # same number of entries as the real rule, on random days
            k = int(sig["entry"].sum())
            mask = np.zeros(len(d), bool)
            if k:
                mask[rng.choice(np.arange(200, len(d) - 1), size=min(k, len(d) - 201), replace=False)] = True
        trades += [{**t, "symbol": sym} for t in simulate(d, sig, exit_, start, end, slip_bps, mask)]
    return trades


def _sel(trades, a, b):
    return [t["r"] for t in trades if a <= t["entry_date"] <= b]


def short_test(bars: dict, spy_close, today: date, months: int = 3, slip_bps: float = 10.0) -> dict:
    """Last `months` months, two halves, 2x slippage (10 bps a side). Returns {strategy: {exit: row}} with a 'pass' flag."""
    a = today - timedelta(days=int(months * 30.5))
    mid = a + (today - a) / 2
    res = {}
    for st in STRATEGIES:
        rows = {}
        for ex in EXITS:
            tr = run(st, bars, ex, a, today - timedelta(days=1), slip_bps, spy_close)
            h1, h2, al = stats(_sel(tr, a, mid)), stats(_sel(tr, mid + timedelta(days=1), today)), stats([t["r"] for t in tr])
            rows[ex] = {"all": al, "half1": h1, "half2": h2}
        own_r = rows["own"]["all"]["total_r"]
        for ex, r in rows.items():
            r["pass"] = bool(ex != "own" and r["all"]["trades"] >= (2 if st == "index200" else 10)
                             and all((r[k]["pf"] or 0) >= 1.0 for k in ("half1", "half2") if r[k]["trades"])
                             and r["all"]["total_r"] > own_r)
        res[st] = rows
    return res


def best_passers(short: dict, k: int = 3) -> dict:
    return {st: [ex for ex, _ in sorted(((ex, r) for ex, r in rows.items() if r["pass"]), key=lambda x: -(x[1]["all"]["pf"] or 0))[:k]]
            for st, rows in short.items()}


def long_test(bars: dict, spy_close, today: date, picks: dict, slip_bps: float = 10.0, first: date = date(2016, 1, 4),
              seeds: int = 5) -> dict:
    """2016 .. (today - 3 months); the last 12 months of that are the sequestered OOS window. Own exits always included as the control."""
    end = today - timedelta(days=92)
    oos_a = end - timedelta(days=365)
    res = {}
    for st, exs in picks.items():
        if not exs:
            continue
        rows = {}
        for ex in ["own"] + [e for e in exs if e != "own"]:
            tr = run(st, bars, ex, first, end, slip_bps, spy_close)
            years = sorted({t["entry_date"].year for t in tr})
            by_year = {y: stats([t["r"] for t in tr if t["entry_date"].year == y]) for y in years}
            plc = [stats([t["r"] for t in run(st, bars, ex, oos_a, end, slip_bps, spy_close, seed=s)])["avg_r"] for s in range(seeds)]
            plc = [p for p in plc if p is not None]
            rows[ex] = {"in_sample": stats(_sel(tr, first, oos_a - timedelta(days=1))), "oos": stats(_sel(tr, oos_a, end)),
                        "years_pf_gt_1": sum(1 for v in by_year.values() if (v["pf"] or 0) > 1), "years": len(by_year),
                        "by_year": {str(y): v for y, v in by_year.items()},
                        "random_entry_oos_avg_r": round(float(np.mean(plc)), 3) if plc else None}
        own_oos = rows["own"]["oos"]["total_r"]
        for ex, r in rows.items():
            o = r["oos"]
            r["keep"] = bool(ex != "own" and o["trades"] >= (10 if st == "index200" else 40) and (o["pf"] or 0) >= 1.15 and o["total_r"] > own_oos
                             and r["years_pf_gt_1"] > r["years"] / 2
                             and (r["random_entry_oos_avg_r"] is None or (o["avg_r"] or -9) > r["random_entry_oos_avg_r"]))
        res[st] = rows
    return res


def _fmt(s: dict) -> str:
    return f"{s['trades']} tr, PF {s['pf']}, win {s['win']}, {s['total_r']:+}R ({s['ret_pct']:+}% at 0.5% risk)"


def short_lines(short: dict) -> list[str]:
    out = []
    for st, rows in short.items():
        out.append(f"### {st} (short test, 2x slippage)")
        for ex, r in rows.items():
            out.append(f"- {ex}: {_fmt(r['all'])} | half1 PF {r['half1']['pf']} ({r['half1']['trades']}) | half2 PF {r['half2']['pf']} "
                       f"({r['half2']['trades']}){' -> PASS' if r['pass'] else ''}")
    return out


def long_lines(long: dict, label: str) -> list[str]:
    out = []
    for st, rows in long.items():
        out.append(f"### {st} long test on {label} (2x slippage)")
        for ex, r in rows.items():
            out.append(f"- {ex}: in-sample {_fmt(r['in_sample'])} | OOS {_fmt(r['oos'])} | years PF>1 {r['years_pf_gt_1']}/{r['years']} | "
                       f"random-entry OOS avg R {r['random_entry_oos_avg_r']}{' -> KEEP' if r['keep'] else (' -> reject' if ex != 'own' else ' (control)')}")
            if ex == "own":
                out.append("  by year: " + ", ".join(f"{y} PF {v['pf']} {v['total_r']:+}R ({v['trades']})" for y, v in r["by_year"].items()))
    return out
