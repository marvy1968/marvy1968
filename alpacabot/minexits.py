"""Minute-bar exit lab for the live intraday sleeves (owner 2026-11-04: "build the minute-bar version for VWAP and Velez").

Entries are the sleeves' own (alpacabot.vwapday ORB / reclaim / band, both sides, spec defaults, in-play names; alpacabot.velez trend /
exhaustion, long), and so is the initial stop. Only the exit changes. Fills: 1-minute bars, the stop checked first inside a minute, a gap
through the stop fills at the open, close-based rules act on CLOSED 5-minute bars and fill at the next minute's open, everything flat by
the sleeve's own end of day (15:50; 13:50 on half days). Costs as each harness (3 bps a side + SEC; 2x = 6 bps).

EXITS, fixed before any run (the forex / swing lab's set on 5-minute bars, plus Muse's):
  own            the sleeve's live exits (unchanged)
  fixed_1r/2r/3r target at 1 / 2 / 3 R          time_30 / time_60   out after 30 / 60 minutes
  atr_trail_2/3  best price -/+ 2 / 3 x the 5-minute ATR at entry
  sma20_close / sma50_close   out on a 5-minute close through the 5-minute 20 / 50 average
  swing_5        stop under the last five 5-minute lows (over the highs for shorts)
  be1r_atr2      break-even at +1R, then a 2 ATR trail
  ponsi          break-even after a 5-minute close at +1R, then trail at the 5-minute 10 EMA -/+ 0.5 ATR
  raschke        every hour (12 five-minute bars) the stop moves to the prior hour's low - 1 cent (high + 1 cent); only tightens
  turtle         out on a 5-minute close beyond the prior 24 five-minute bars' (2 hours) low / high
  wstair / wstair_ag   trail at the best price -/+ 0.50 / 0.40 / 0.30 / 0.20 R by this run's realized R this week (Monday reset);
                 aggressive 0.50 / 0.30 / 0.20 / 0.10
  crabel (control)     the initial stop only, flat at the day's end      williams (control)  out at the next open after the first
                 profitable 5-minute close
On minute bars Raschke's "prior day" becomes the prior hour and Turtle's "10 days" becomes 2 hours: the rules' shape, scaled to the session.

PRE-REGISTERED bar (short history only - a few months of minute bars, no 5-year test is possible here):
  per sleeve strategy (orb / reclaim / band with both sides pooled, velez): 30+ trades, PF >= 1.0 in each half of the period at 2x costs,
  total R at 2x above the sleeve's own exits at 2x, AND better PF than the same exit on random entries in 16 of 20 seeds.
  Anything that passes is a candidate for paper, never "proven"."""

import numpy as np

EXITS_MIN = ("own", "fixed_1r", "fixed_2r", "fixed_3r", "time_30", "time_60", "atr_trail_2", "atr_trail_3", "sma20_close", "sma50_close",
             "swing_5", "be1r_atr2", "ponsi", "raschke", "turtle", "wstair", "wstair_ag", "crabel", "williams")
OVERRIDE = {"exit": None}            # set by run_all; vwapday.walk / velez.walk hand the trade to min_walk when it is not None / "own"
CUR = {"week": None}                 # the harness sets the current ISO week; realized R per week feeds the weekly stair
WEEK_R: dict = {}


def reset():
    WEEK_R.clear()
    CUR["week"] = None


def _bars5(sd: dict):
    o, h, l, c = (np.asarray(sd[k], float) for k in ("o", "h", "l", "c"))
    n = len(c) // 5
    if n == 0:
        z = np.zeros(0)
        return z, z, z, z
    h5 = h[:n * 5].reshape(n, 5).max(axis=1)
    l5 = l[:n * 5].reshape(n, 5).min(axis=1)
    c5 = c[4:n * 5:5]
    o5 = o[0:n * 5:5]
    return o5, h5, l5, c5


def _ind(sd: dict):
    if "_mx" in sd:
        return sd["_mx"]
    o5, h5, l5, c5 = _bars5(sd)
    n = len(c5)
    pc = np.r_[c5[0] if n else np.nan, c5[:-1]]
    tr = np.maximum.reduce([h5 - l5, np.abs(h5 - pc), np.abs(l5 - pc)]) if n else np.zeros(0)
    atr = np.full(n, np.nan)
    for k in range(n):
        lo = max(0, k - 13)
        atr[k] = tr[lo:k + 1].mean()
    def sma(x, w):
        out = np.full(len(x), np.nan)
        cs = np.cumsum(np.r_[0.0, x])
        for k in range(w - 1, len(x)):
            out[k] = (cs[k + 1] - cs[k + 1 - w]) / w
        return out
    ema = np.full(n, np.nan)
    if n:
        a = 2 / 11
        ema[0] = c5[0]
        for k in range(1, n):
            ema[k] = a * c5[k] + (1 - a) * ema[k - 1]
    res = {"h5": h5, "l5": l5, "c5": c5, "atr": atr, "s20": sma(c5, 20), "s50": sma(c5, 50), "ema": ema}
    sd["_mx"] = res
    return res


def min_walk(ex: str, sd: dict, side: int, j: int, e: float, stop: float, end: int):
    """One trade from minute j (fill e) with the initial stop; returns (events [(minute, frac, px, reason)], mfe_R, mae_R)."""
    o, h, l, c = (np.asarray(sd[k], float) for k in ("o", "h", "l", "c"))
    n = len(o)
    end = min(end, n - 1)
    R0 = side * (e - stop)
    I = _ind(sd)
    kb = max(0, j // 5 - 1)                                       # the last closed 5-minute bar at entry
    a0 = float(I["atr"][kb]) if len(I["atr"]) and np.isfinite(I["atr"][kb]) else R0
    tgt = {"fixed_1r": e + side * R0, "fixed_2r": e + side * 2 * R0, "fixed_3r": e + side * 3 * R0}.get(ex)
    best, worst, armed = e, e, False
    week = CUR["week"]

    def done(i, px, why):
        r = side * (px - e) / R0
        if week is not None:
            WEEK_R[week] = WEEK_R.get(week, 0.0) + r
        return [(i, 1.0, float(px), why)], side * (best - e) / R0, side * (worst - e) / R0

    for i in range(j, end):
        if i > j and side * (o[i] - stop) <= 0:
            return done(i, o[i], "stop")
        if (l[i] <= stop) if side == 1 else (h[i] >= stop):
            worst = min(worst, l[i]) if side == 1 else max(worst, h[i])
            return done(i, stop, "stop")
        if tgt is not None:
            if i > j and side * (o[i] - tgt) >= 0:
                return done(i, o[i], "target")
            if (h[i] >= tgt) if side == 1 else (l[i] <= tgt):
                return done(i, tgt, "target")
        best = max(best, h[i]) if side == 1 else min(best, l[i])
        worst = min(worst, l[i]) if side == 1 else max(worst, h[i])
        held = i - j + 1
        if (ex == "time_30" and held >= 30) or (ex == "time_60" and held >= 60):
            return done(i + 1, o[i + 1], "time")
        if (i + 1) % 5 != 0:
            continue
        k = (i + 1) // 5 - 1                                      # this 5-minute bar just closed
        if k >= len(I["c5"]):
            continue
        c5 = float(I["c5"][k])
        flag = False
        if ex == "sma20_close" and np.isfinite(I["s20"][k]):
            flag = side * (c5 - I["s20"][k]) < 0
        elif ex == "sma50_close" and np.isfinite(I["s50"][k]):
            flag = side * (c5 - I["s50"][k]) < 0
        elif ex == "turtle" and k >= 24:
            lo, hi = float(np.min(I["l5"][k - 24:k])), float(np.max(I["h5"][k - 24:k]))
            flag = (c5 < lo) if side == 1 else (c5 > hi)
        elif ex == "williams":
            flag = side * (c5 - e) > 0
        if flag:
            return done(i + 1, o[i + 1], "signal")
        lvl = None
        if ex in ("atr_trail_2", "atr_trail_3"):
            lvl = best - side * (2.0 if ex == "atr_trail_2" else 3.0) * a0
        elif ex == "swing_5":
            lo_k = max(0, k - 4)
            lvl = float(np.min(I["l5"][lo_k:k + 1])) if side == 1 else float(np.max(I["h5"][lo_k:k + 1]))
        elif ex == "be1r_atr2" and side * (best - e) >= R0:
            t2 = best - side * 2.0 * a0
            lvl = t2 if side * (t2 - e) > 0 else e
        elif ex == "ponsi":
            if not armed and side * (c5 - e) >= R0:
                armed = True
            if armed and np.isfinite(I["ema"][k]):
                t2 = float(I["ema"][k]) - side * 0.5 * a0
                lvl = t2 if side * (t2 - e) > 0 else e
        elif ex == "raschke" and (k + 1 - (j // 5)) % 12 == 0 and k >= 11:
            lvl = float(np.min(I["l5"][k - 11:k + 1])) - 0.01 if side == 1 else float(np.max(I["h5"][k - 11:k + 1])) + 0.01
        elif ex in ("wstair", "wstair_ag"):
            wr = WEEK_R.get(week, 0.0) if week is not None else 0.0
            tiers = (0.50, 0.40, 0.30, 0.20) if ex == "wstair" else (0.50, 0.30, 0.20, 0.10)
            dist = tiers[0] if wr <= 0 else tiers[1] if wr <= 2 else tiers[2] if wr <= 5 else tiers[3]
            lvl = best - side * dist * R0
        if lvl is not None and side * (lvl - stop) > 0:
            stop = lvl
    return done(end, o[end] if end < n else c[n - 1], "eod")


def _pf(rs) -> float:
    rs = np.asarray(list(rs), float)
    w, lo = rs[rs > 0].sum(), -rs[rs < 0].sum()
    return float(w / lo) if lo > 0 else (99.0 if w > 0 else 0.0)


def _row(rows1, rows2, split):
    a = [r for _, r in rows1]
    h1 = [r for d, r in rows2 if d < split]
    h2 = [r for d, r in rows2 if d >= split]
    return {"n": len(a), "pf": _pf(a), "net": float(sum(a)), "net2x": float(sum(r for _, r in rows2)), "pf_h1_2x": _pf(h1), "n_h1": len(h1),
            "pf_h2_2x": _pf(h2), "n_h2": len(h2), "avg": float(np.mean(a)) if a else 0.0}


def run_all(load, today, out_root, log=print, seeds: int = 20, vwap_days_back: int = 150, velez_days_back: int = 120) -> list[str]:
    from datetime import timedelta  # noqa: F401
    from pathlib import Path

    from . import velez as ve
    from . import vwapday as vd
    out = Path(out_root) / f"minexits-{today:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["## Minute-bar exit lab (alpacabot/minexits.py) - pre-registered: 30+ trades, PF >= 1.0 in each half at 2x costs, total R at 2x "
             "above own at 2x, and beats the same exit on random entries in 16/20 seeds. Short minute history: a candidate, never proof."]
    ctx, test_days, period = vd.quick_ctx(load, today, sessions=None, days_back=vwap_days_back, log=lambda *_: None)
    split_v = test_days[len(test_days) // 2]
    P = ve.prep(load, today, log=lambda *_: None, days_back=velez_days_back)
    split_z = P["test_days"][len(P["test_days"]) // 2]
    table = {}
    try:
        for ex in EXITS_MIN:
            OVERRIDE["exit"] = None if ex == "own" else ex
            for strat in ("orb", "reclaim", "band"):
                r1, r2, trades = [], [], []
                for side in (1, -1):
                    reset()
                    tr, *_ = vd.run(ctx, strat, side, vd.DEFAULT[strat], period=period, swing=False)
                    reset()
                    tr2, *_ = vd.run(ctx, strat, side, vd.DEFAULT[strat], mult=2.0, period=period, swing=False)
                    r1 += [(t["date"], t["R"]) for t in tr]
                    r2 += [(t["date"], t["R"]) for t in tr2]
                    trades += [(strat, t) for t in tr]
                table[(f"vwap {strat}", ex)] = {**_row(r1, r2, split_v), "_trades": trades}
            reset()
            tz = ve.run_trades(P)
            reset()
            tz2 = ve.run_trades(P, mult=2.0)
            table[("velez", ex)] = {**_row([(t["date"], t["R"] * t["w"]) for t in tz], [(t["date"], t["R"] * t["w"]) for t in tz2], split_z),
                                    "_trades": []}
            log(f"exit {ex} done")
        # random-entry control for every exit that clears the rest of the bar
        for (name, ex), row in table.items():
            own = table[(name, "own")]
            row["pre"] = bool(ex != "own" and row["n"] >= 30 and row["n_h1"] > 0 and row["n_h2"] > 0 and row["pf_h1_2x"] >= 1.0
                              and row["pf_h2_2x"] >= 1.0 and row["net2x"] > own["net2x"])
            row["ctrl"] = None
            if not row["pre"]:
                continue
            OVERRIDE["exit"] = ex
            beat = 0
            if name == "velez":
                for s_ in range(seeds):
                    reset()
                    rz = ve.run_trades(P, rng=np.random.default_rng(s_))
                    beat += row["pf"] > _pf(t["R"] * t["w"] for t in rz)
            else:
                strat = name.split()[1]
                by_side = {}
                for st_, t in row["_trades"]:
                    by_side.setdefault(t["side"], []).append(t)
                rnd = [[] for _ in range(seeds)]
                for side, ts in by_side.items():
                    rr, _ = vd.controls(ctx, strat, ts, seeds)
                    for k_, v_ in enumerate(rr):
                        rnd[k_].append(v_)
                beat = sum(row["pf"] > float(np.nanmean(v)) for v in rnd if v)
            row["ctrl"] = int(beat)
    finally:
        OVERRIDE["exit"] = None
        reset()
    for name in ("vwap orb", "vwap reclaim", "vwap band", "velez"):
        split = split_z if name == "velez" else split_v
        lines += [f"### {name} (halves split at {split})",
                  "| Exit | Trades | PF | Avg R | Net R | Net R @2x | Half 1 PF @2x (n) | Half 2 PF @2x (n) | Random /20 | Verdict |",
                  "|" + "---|" * 10]
        for ex in EXITS_MIN:
            r = table[(name, ex)]
            verdict = "own (control)" if ex == "own" else ("control" if ex in ("crabel", "williams") and not r["pre"] else
                                                          ("PASS" if r["pre"] and (r["ctrl"] or 0) >= 0.8 * seeds else "fail"))
            lines.append(f"| {ex} | {r['n']} | {r['pf']:.2f} | {r['avg']:+.3f} | {r['net']:+.1f} | {r['net2x']:+.1f} | {r['pf_h1_2x']:.2f} "
                         f"({r['n_h1']}) | {r['pf_h2_2x']:.2f} ({r['n_h2']}) | {'-' if r['ctrl'] is None else r['ctrl']} | {verdict} |")
    ok = [f"{name}:{ex}" for (name, ex), r in table.items() if r.get("pre") and (r.get("ctrl") or 0) >= 0.8 * seeds and ex not in ("crabel", "williams")]
    lines.append(f"- VERDICT: {ok or 'nothing passes - the sleeves keep their own exits'}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return lines
