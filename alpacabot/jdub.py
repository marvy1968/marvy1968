"""Jdub Trades' "9:30 AM" 1-minute scalp (owner 2026-10-29: "analyze Jdub, test it and hybrid it with ours, then retest").

PRE-DECLARED before any run, on Alpaca 1-minute bars of the 55-stock list + SPY / QQQ:
  OR       the 9:30-9:35 range (first 5-minute candle): its high and low
  BREAK    the first 1-minute close above the OR high (long) / below the OR low (short), 9:35-10:30
  FVG      (variant) the breakout leg leaves a fair value gap: a bar whose low is above the high two bars earlier (long; short mirrored)
  RETEST   after the break, a 1-minute bar trades back to the OR level (low <= OR high, long) and CLOSES back beyond it -> buy at the next
           bar's open (no chasing the breakout itself); stop = that retest bar's low (short: high); target 2R (and 3R); flat at 10:30
           at the latest (the method is a first-hour scalp). One trade per stock per day.
  costs    5 bps a side (10 bps as the stress)
Our HYBRID variants (the bot's own rules on top):
  H1  + long-trend filter (fxbot.strongtrend votes on daily bars): no long against a long DOWN trend, no short against a long UP trend
  H2  H1 + market agrees: SPY above its 9:30 open for longs, below for shorts, at the retest bar
  H3  H2 + news pause (no entry within 60 min of a high-impact USD release, fxbot.newsfilter calendar) and FVG required
Control: the same number of trades at random minutes 9:35-10:30 of the same days, random side, the same stop size (in % of price) and
target, 20 seeds. Read: the last 2 weeks (owner) and the last 92 days at 10 bps; a variant PASSES on 92 days with 30+ trades, net R > 0,
PF >= 1.2 and beating 18 of 20 random sets.
"""

from datetime import date, time as dtime, timedelta

import numpy as np
import pandas as pd

NY = "America/New_York"
END = dtime(10, 30)


def sessions(m1: pd.DataFrame) -> dict:
    """{date: 1-minute frame 09:30-10:30 New York} (naive New York times)."""
    x = m1.copy()
    x.index = x.index.tz_convert(NY)
    x = x[(x.index.time >= dtime(9, 30)) & (x.index.time < END)]
    return {d: g for d, g in x.groupby(x.index.date) if len(g) >= 30}


def day_setups(g: pd.DataFrame, need_fvg: bool = False) -> list[dict]:
    """The day's Jdub setups (at most one): entry bar index, side, stop, OR level."""
    o, h, l, c = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = g.index.time
    orb = np.flatnonzero(np.array([x < dtime(9, 35) for x in t]))
    if len(orb) < 3:
        return []
    orh, orl = h[orb].max(), l[orb].min()
    start = orb[-1] + 1
    brk, side = None, 0
    for i in range(start, len(g)):
        if c[i] > orh:
            brk, side = i, 1
            break
        if c[i] < orl:
            brk, side = i, -1
            break
    if brk is None:
        return []
    lvl = orh if side == 1 else orl
    fvg = False
    for i in range(brk + 1, len(g) - 1):
        if i >= 2 and ((side == 1 and l[i] > h[i - 2]) or (side == -1 and h[i] < l[i - 2])):
            fvg = True
        touched = (l[i] <= lvl) if side == 1 else (h[i] >= lvl)
        back = (c[i] > lvl) if side == 1 else (c[i] < lvl)
        if touched and back:
            if need_fvg and not fvg:
                return []
            stop = l[i] if side == 1 else h[i]
            return [{"i": i + 1, "side": side, "stop": stop, "lvl": lvl, "fvg": fvg, "retest": i}]
    return []


def walk(g: pd.DataFrame, i: int, side: int, stop: float, target_r: float, bps: float) -> float | None:
    """R of a trade entered at bar i's open (cost bps a side), stop first when a bar touches both, flat at the last bar's close."""
    o, h, l, c = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    if i >= len(g):
        return None
    fill = o[i] * (1 + side * bps / 1e4)
    risk = side * (fill - stop)
    if risk <= 0:
        return None
    tgt = fill + side * target_r * risk
    for k in range(i, len(g)):
        if (side == 1 and l[k] <= stop) or (side == -1 and h[k] >= stop):
            px = o[k] if k > i and side * (o[k] - stop) <= 0 else stop
            break
        if (side == 1 and h[k] >= tgt) or (side == -1 and l[k] <= tgt):
            px = o[k] if k > i and side * (o[k] - tgt) >= 0 else tgt
            break
    else:
        px = c[-1]
    px = px * (1 - side * bps / 1e4)
    return side * (px - fill) / risk


def run(days: dict, a: date, b: date, variant: str, target_r: float, bps: float, trend: dict, spy: dict, news) -> list[dict]:
    """days = {symbol: {date: 1-minute session frame}}; trend = {symbol: {date: long_state}}; spy = {date: SPY session frame}."""
    out = []
    for sym, dd in days.items():
        for d, g in dd.items():
            if not (a <= d <= b):
                continue
            for s in day_setups(g, need_fvg=(variant in ("FVG", "H3"))):
                side = s["side"]
                if variant in ("H1", "H2", "H3"):
                    ls = trend.get(sym, {}).get(d, 0)
                    if ls == -side:
                        continue
                if variant in ("H2", "H3"):
                    sg = spy.get(d)
                    if sg is None:
                        continue
                    t_ret = g.index[s["retest"]]
                    sp_now = sg[sg.index <= t_ret]
                    if not len(sp_now) or side * (sp_now["close"].iloc[-1] - sg["open"].iloc[0]) <= 0:
                        continue
                if variant == "H3" and news(g.index[s["i"]]):
                    continue
                r = walk(g, s["i"], side, s["stop"], target_r, bps)
                if r is not None:
                    out.append({"sym": sym, "date": d, "side": side, "r": r,
                                "stop_pct": abs(g["open"].iloc[s["i"]] - s["stop"]) / g["open"].iloc[s["i"]]})
    return out


def control(days: dict, real: list[dict], target_r: float, bps: float, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    rs = []
    for t in real:
        g = days[t["sym"]][t["date"]]
        cand = np.flatnonzero(np.array([x >= dtime(9, 36) for x in g.index.time]))[:-1]
        if not len(cand):
            continue
        i = int(rng.choice(cand))
        side = 1 if rng.random() < 0.5 else -1
        px = g["open"].iloc[i]
        r = walk(g, i, side, px * (1 - side * t["stop_pct"]), target_r, bps)
        if r is not None:
            rs.append(r)
    return rs


def summary(rs) -> dict:
    rs = np.asarray(rs, float)
    if not len(rs):
        return {"trades": 0, "net": 0.0, "pf": 0.0, "win": 0.0}
    w, lo = rs[rs > 0].sum(), -rs[rs <= 0].sum()
    return {"trades": len(rs), "net": float(rs.sum()), "pf": float(w / lo) if lo > 0 else 99.0, "win": float((rs > 0).mean())}


def fmt(s: dict) -> str:
    return f"{s['trades']} t, win {s['win']:.0%}, PF {s['pf']:.2f}, {s['net']:+.1f}R"


def report(m1: dict, daily: dict, today: date, news=lambda ts: False, seeds: int = 20) -> list[str]:
    """m1 = {symbol: 1-minute bars (UTC)}, daily = {symbol: daily bars} for the long-trend filter."""
    from fxbot.strongtrend import votes
    days = {s: sessions(d) for s, d in m1.items() if len(d)}
    spy = days.get("SPY", {})
    trend = {}
    for s, d in daily.items():
        if len(d) > 270:
            v = votes(d)["long_state"]
            idx = [pd.Timestamp(x).tz_convert(NY).date() if pd.Timestamp(x).tzinfo else pd.Timestamp(x).date() for x in v.index]
            ser = pd.Series(v.to_numpy(), index=idx).shift(1)            # the last COMPLETED day's reading
            trend[s] = ser.dropna().astype(int).to_dict()
    out = [f"## jdub_930: 5-minute opening range, 1-minute break, buy the RETEST (not the breakout), stop under the retest bar, flat 10:30; "
           f"{len(days)} stocks"]
    for label, a in (("last 2 weeks", today - timedelta(days=14)), ("last 92 days", today - timedelta(days=92))):
        out.append(f"### {label} ({a}..{today})")
        for variant in ("JDUB", "FVG", "H1", "H2", "H3"):
            for tr in (2.0, 3.0):
                r5 = run(days, a, today, variant, tr, 5.0, trend, spy, news)
                r10 = run(days, a, today, variant, tr, 10.0, trend, spy, news)
                s10 = summary([t["r"] for t in r10])
                ctrl = [summary(control(days, r10, tr, 10.0, k))["net"] for k in range(seeds)]
                beat = sum(s10["net"] > x for x in ctrl)
                ok = s10["trades"] >= 30 and s10["net"] > 0 and s10["pf"] >= 1.2 and beat >= round(0.9 * seeds)
                out.append(f"- {variant} {tr:g}R: 5 bps {fmt(summary([t['r'] for t in r5]))} | 10 bps {fmt(s10)} | longs "
                           f"{fmt(summary([t['r'] for t in r10 if t['side'] == 1]))} | shorts {fmt(summary([t['r'] for t in r10 if t['side'] == -1]))} | "
                           f"random median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds}"
                           + (f" | {'PASS' if ok else 'fail'}" if label == "last 92 days" else ""))
    return out


# ---- Jdub's ideas grafted onto OUR same-day ride (owner 2026-10-29: "can we hybrid his ideas to improve our trade system") -------------------
# PRE-DECLARED before any run. Base = the live day ride (fxbot.strongtrend: first hard-move bar 10:00-15:00 on 5-minute bars, next open,
# stop 0.5 daily ATR, out on the 5-minute reversal or at 15:55). Each Jdub idea is added on its own, then all together:
#   OR   the hard move must have broken the 9:30-10:00 opening range its way (close beyond the OR high / low)
#   FVG  the move left a fair value gap in the last 6 bars (a bar's low above the high two bars earlier; shorts mirrored)
#   RT   don't chase: a limit order 0.15 daily ATR back toward the move, good for 60 minutes (no fill = no trade); same 0.5 ATR stop from the fill
#   2R   Jdub's fixed target: take profit at 2R (else the ride's own exits)
#   ALL  OR + FVG + RT; ALL2R = ALL + 2R
# Costs 10 bps a side. Control: every variant re-run on shuffled days of the hard-move signal (20 seeds). A variant is ADOPTED only if, on the
# last 92 days, it has 30+ trades, net R > 0, PF >= 1.2, beats the base's net R, and beats 18 of 20 shuffled runs; the last 2 weeks shown too.
VARIANTS = {"BASE": set(), "OR": {"or"}, "FVG": {"fvg"}, "RT": {"rt"}, "2R": {"2r"}, "ALL": {"or", "fvg", "rt"}, "ALL2R": {"or", "fvg", "rt", "2r"}}
PULLBACK_ATR, PULLBACK_BARS, FVG_LOOKBACK = 0.15, 12, 6


def ride_hybrid(m5: pd.DataFrame, ba: pd.DataFrame, td: np.ndarray, atr_bar: np.ndarray, rv: np.ndarray, rules: set) -> list[dict]:
    from fxbot.strongtrend import RIDE_STOP_ATR, day_keys, windows
    key = day_keys(m5.index, False)
    ok, last = windows(m5.index, False)
    ny = m5.index.tz_convert(NY)
    mins = ny.hour * 60 + ny.minute
    h, l, c = (m5[k].to_numpy(float) for k in ("high", "low", "close"))
    bo, bh, bl, ao, ah, al = (ba[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    in_or = (mins >= 9 * 60 + 30) & (mins < 10 * 60)
    orh = pd.Series(np.where(in_or, h, np.nan)).groupby(key).transform("max").to_numpy(float)
    orl = pd.Series(np.where(in_or, l, np.nan)).groupby(key).transform("min").to_numpy(float)
    out, done, n, i = [], set(), len(m5), 0
    while i < n - 1:
        side = int(td[i])
        if not side or not ok[i] or key[i] in done or not np.isfinite(atr_bar[i]):
            i += 1
            continue
        done.add(key[i])
        if "or" in rules and not (np.isfinite(orh[i]) and ((side == 1 and c[i] > orh[i]) or (side == -1 and c[i] < orl[i]))):
            i += 1
            continue
        if "fvg" in rules:
            ks = [k for k in range(max(2, i - FVG_LOOKBACK + 1), i + 1) if key[k] == key[i] and key[k - 2] == key[i]]
            if not any((side == 1 and l[k] > h[k - 2]) or (side == -1 and h[k] < l[k - 2]) for k in ks):
                i += 1
                continue
        sd = RIDE_STOP_ATR * atr_bar[i]
        j, fill = i + 1, None
        if "rt" in rules:
            lvl = c[i] - side * PULLBACK_ATR * atr_bar[i]
            for q in range(i + 1, min(n, i + 1 + PULLBACK_BARS)):
                if key[q] != key[i] or last[q]:
                    break
                if side == 1 and al[q] <= lvl:
                    j, fill = q, min(ao[q], lvl)
                    break
                if side == -1 and bh[q] >= lvl:
                    j, fill = q, max(bo[q], lvl)
                    break
            if fill is None:
                i += 1
                continue
        else:
            fill = ao[j] if side == 1 else bo[j]
        stop, tgt = fill - side * sd, fill + side * 2 * sd
        k, px = j, None
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            if key[k] != key[i]:
                px = op
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px = op if side * (op - stop) <= 0 and k > j else stop
            elif "2r" in rules and ((side == 1 and hi >= tgt) or (side == -1 and lo <= tgt)):
                px = op if side * (op - tgt) >= 0 and k > j else tgt
            elif (last[k] or rv[k] == -side) and k + 1 < n:
                k += 1
                px = bo[k] if side == 1 else ao[k]
            if px is not None:
                break
            k += 1
        if px is None:
            px = bo[n - 1] if side == 1 else ao[n - 1]
        out.append({"date": pd.Timestamp(key[i]).date(), "side": side, "r": side * (px - fill) / sd})
        i = max(k, i + 1)
    return out


def hybrid_report(five: dict, daily: dict, today: date, seeds: int = 20) -> list[str]:
    """five = {symbol: 5-minute bars (UTC)}, daily = {symbol: daily bars}."""
    from alpacabot.trendzones import to_bidask
    from fxbot.strongtrend import daily_atr_on, day_keys, permute_days, reversal, today_state
    pre = {}
    for s, m5 in five.items():
        if s in daily and len(m5) >= 300 and len(daily[s]) > 220:
            m5 = m5.astype(float)
            pre[s] = (m5, to_bidask(m5, 10.0), today_state(m5, daily[s], False), daily_atr_on(m5.index, daily[s], False), reversal(m5),
                      day_keys(m5.index, False))

    def trades(rules, a, seed=None):
        rs = []
        for s, (m5, ba, td, at, rv, key) in pre.items():
            t_ = td if seed is None else permute_days(td, key, seed)
            rs += [t for t in ride_hybrid(m5, ba, t_, at, rv, rules) if a <= t["date"] <= today]
        return rs
    out = [f"## jdub_hybrid: Jdub's ideas (opening-range break, FVG, buy the pullback not the breakout, 2R target) on OUR day ride; "
           f"{len(pre)} stocks, 10 bps a side, shuffled-day controls x{seeds}"]
    for label, a in (("last 2 weeks", today - timedelta(days=14)), ("last 92 days", today - timedelta(days=92))):
        out.append(f"### {label} ({a}..{today})")
        base = summary([t["r"] for t in trades(VARIANTS["BASE"], a)])
        for name, rules in VARIANTS.items():
            tt = trades(rules, a)
            s = summary([t["r"] for t in tt])
            ctrl = [summary([t["r"] for t in trades(rules, a, k)])["net"] for k in range(seeds)]
            beat = sum(s["net"] > x for x in ctrl)
            ok = s["trades"] >= 30 and s["net"] > 0 and s["pf"] >= 1.2 and beat >= round(0.9 * seeds) and (name == "BASE" or s["net"] > base["net"])
            out.append(f"- {name}: {fmt(s)} | longs {fmt(summary([t['r'] for t in tt if t['side'] == 1]))} | shorts "
                       f"{fmt(summary([t['r'] for t in tt if t['side'] == -1]))} | shuffled median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds}"
                       + (f" | {'PASS' if ok else 'fail'}" if label == "last 92 days" else ""))
    return out
