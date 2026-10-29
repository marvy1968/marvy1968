"""Sneaky Pivot (owner 2026-10-28: "deep search sneaky pivot ... ok build and test").

PRE-DECLARED before any run - a mechanical reading of the YouTube method ("Doug's Sneaky Pivot"; the one public coded version lost
-6.79R on 69 BTC trades, 2020-21):
- Bars: 15 minutes. Levels: the PREVIOUS day's high and low (forex day = 17:00 New York; stocks = the regular session).
- Long setup at yesterday's low: a 15-minute bar trades at or below it and CLOSES back above it as an up candle (close > open). Short:
  the mirror at yesterday's high. Each level once per day; one trade per instrument at a time.
- Entry: a buy-stop at that candle's high (sell-stop at its low), live for the NEXT candle only, else cancelled.
- Stop: the setup candle's low - 0.1 x ATR(14, 15-minute) (short: its high + 0.1 ATR). Target: 2R. Out at the session end if neither is
  hit (forex 16:00 New York, stocks 15:55). Entries: forex 02:00-12:00 New York (London + New York morning), stocks 09:45-15:00.
- Costs: forex OANDA bid/ask (x1 and x2 spread); stocks 5 bps a side (and 10).
- Control: the same number of trades on random 15-minute bars of the same days, random side, the same stop size in ATR and 2R target, 20 seeds.
- PASS: over the long window AND the last 92 days, at 2x costs: 30+ trades, net R > 0, PF >= 1.2, and beats 18 of 20 random controls.
  The last 2 weeks are shown for the owner but too short to judge.
"""

from datetime import date, time as dtime, timedelta

import numpy as np
import pandas as pd

NY = "America/New_York"
TARGET_R, BUF_ATR = 2.0, 0.1
WINDOWS = {True: (dtime(2, 0), dtime(12, 0), dtime(16, 0)), False: (dtime(9, 45), dtime(15, 0), dtime(15, 55))}


def resample15(df: pd.DataFrame) -> pd.DataFrame:
    """bid/ask (bo..ac) bars of any finer size -> 15-minute bars."""
    agg = {}
    for s in "ba":
        agg.update({s + "o": "first", s + "h": "max", s + "l": "min", s + "c": "last"})
    return df[[c for c in agg if c in df]].resample("15min", label="left", closed="left").agg(agg).dropna()


def day_key(idx: pd.DatetimeIndex, forex: bool) -> np.ndarray:
    ny = idx.tz_convert(NY)
    return np.asarray((ny + pd.Timedelta(hours=7)).date if forex else ny.date)


def prior_levels(m: pd.DataFrame, key: np.ndarray, forex: bool) -> tuple[np.ndarray, np.ndarray]:
    """Yesterday's high / low on every bar (stocks: regular-session bars only make the day's range)."""
    s = pd.DataFrame({"h": m["high"].to_numpy(), "l": m["low"].to_numpy(), "k": key})
    if not forex:
        t = m.index.tz_convert(NY).time
        s = s[[(dtime(9, 30) <= x < dtime(16, 0)) for x in t]]
    g = s.groupby("k").agg(h=("h", "max"), l=("l", "min"))
    prev = g.shift(1)
    return prev["h"].reindex(key).to_numpy(float), prev["l"].reindex(key).to_numpy(float)


def _atr(m: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, c = m["high"], m["low"], m["close"]
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).rolling(n).mean().to_numpy(float)


def _walk(df_arr, side: int, j: int, fill: float, stop: float, tgt: float, key, last) -> tuple[float, int, str]:
    """Exit price, bar and reason from bar j on (stop first when a bar touches both)."""
    bo, bh, bl, bc, ao, ah, al, ac = df_arr
    n = len(bo)
    k = j
    while k < n:
        if key[k] != key[j]:
            return (bo[k] if side == 1 else ao[k]), k, "day"
        lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
        if (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
            return (op if k > j and side * (op - stop) <= 0 else stop), k, "stop"
        if (side == 1 and hi >= tgt) or (side == -1 and lo <= tgt):
            return (op if k > j and side * (op - tgt) >= 0 else tgt), k, "target"
        if last[k]:
            return (bc[k] if side == 1 else ac[k]), k, "session end"
        k += 1
    return (bc[n - 1] if side == 1 else ac[n - 1]), n - 1, "end"


def trades(df: pd.DataFrame, forex: bool, a: date, b: date) -> list[dict]:
    from .data import mid
    m = mid(df)
    key = day_key(df.index, forex)
    pdh, pdl = prior_levels(m, key, forex)
    atr = _atr(m)
    o, h, l, c = (m[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    arr = tuple(df[k].to_numpy(float) for k in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"))
    t0, t1, tend = WINDOWS[forex]
    tt = df.index.tz_convert(NY).time
    entry_ok = np.array([t0 <= x < t1 for x in tt])
    last = np.array([x >= tend for x in tt]) | np.r_[key[1:] != key[:-1], True]
    dates = pd.to_datetime(key).date
    out, used, i, n = [], set(), 0, len(df)
    while i < n - 1:
        if not entry_ok[i] or not (a <= dates[i] <= b) or not np.isfinite(atr[i]) or not np.isfinite(pdl[i]):
            i += 1
            continue
        side = 0
        if l[i] <= pdl[i] < c[i] and c[i] > o[i] and (key[i], "L") not in used:
            side, lvl = 1, "L"
        elif h[i] >= pdh[i] > c[i] and c[i] < o[i] and (key[i], "H") not in used:
            side, lvl = -1, "H"
        if not side:
            i += 1
            continue
        used.add((key[i], lvl))
        j = i + 1
        if key[j] != key[i]:
            i += 1
            continue
        trig = h[i] if side == 1 else l[i]
        stop = (l[i] - BUF_ATR * atr[i]) if side == 1 else (h[i] + BUF_ATR * atr[i])
        bo, bh, bl, bc, ao, ah, al, ac = arr
        hit = ah[j] >= trig if side == 1 else bl[j] <= trig
        if not hit:                                           # the stop order lapses after one candle
            i = j
            continue
        fill = max(trig, ao[j]) if side == 1 else min(trig, bo[j])
        risk = side * (fill - stop)
        if risk <= 0:
            i = j
            continue
        tgt = fill + side * TARGET_R * risk
        px, k, why = _walk(arr, side, j, fill, stop, tgt, key, last)
        out.append({"date": dates[i], "side": side, "r": side * (px - fill) / risk, "why": why, "risk_atr": risk / atr[i], "i": i})
        i = max(k, j) + 1
    return out


def control(df: pd.DataFrame, forex: bool, real: list[dict], seed: int) -> list[float]:
    """Random 15-minute bars of the real trades' days, random side, the real trades' stop size in ATR, the same 2R target."""
    from .data import mid
    if not real:
        return []
    rng = np.random.default_rng(seed)
    m = mid(df)
    key = day_key(df.index, forex)
    atr = _atr(m)
    arr = tuple(df[k].to_numpy(float) for k in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"))
    t0, t1, tend = WINDOWS[forex]
    tt = df.index.tz_convert(NY).time
    entry_ok = np.array([t0 <= x < t1 for x in tt])
    last = np.array([x >= tend for x in tt]) | np.r_[key[1:] != key[:-1], True]
    dates = pd.to_datetime(key).date
    rs = []
    for t in real:
        cand = np.flatnonzero((dates == t["date"]) & entry_ok & np.isfinite(atr))
        cand = cand[cand < len(df) - 1]
        if not len(cand):
            continue
        i = int(rng.choice(cand))
        side = 1 if rng.random() < 0.5 else -1
        j = i + 1
        bo, bh, bl, bc, ao, ah, al, ac = arr
        fill = ao[j] if side == 1 else bo[j]
        risk = t["risk_atr"] * atr[i]
        stop, tgt = fill - side * risk, fill + side * TARGET_R * risk
        px, _, _ = _walk(arr, side, j, fill, stop, tgt, key, last)
        rs.append(side * (px - fill) / risk)
    return rs


def summary(rs) -> dict:
    rs = np.asarray(rs, float)
    if not len(rs):
        return {"trades": 0, "net": 0.0, "pf": 0.0, "win": 0.0}
    w, lo = rs[rs > 0].sum(), -rs[rs <= 0].sum()
    return {"trades": len(rs), "net": float(rs.sum()), "pf": float(w / lo) if lo > 0 else 99.0, "win": float((rs > 0).mean())}


def fmt(s: dict) -> str:
    return f"{s['trades']} t, win {s['win']:.0%}, PF {s['pf']:.2f}, {s['net']:+.1f}R"


def report(market: str, data: dict, forex: bool, today: date, long_from: date, stress, seeds: int = 20) -> list[str]:
    """data = {symbol: 15-minute bid/ask}; stress(df) -> the 2x-cost frame."""
    out = [f"### {market}: {len(data)} symbols, long window {long_from}..{today}"]
    verdicts = []
    for label, a in (("long window", long_from), ("last 92 days", today - timedelta(days=92)), ("last 2 weeks", today - timedelta(days=14))):
        r1 = [t for d in data.values() for t in trades(d, forex, a, today)]
        per2 = {s: trades(stress(d), forex, a, today) for s, d in data.items()}
        r2 = [t for v in per2.values() for t in v]
        s2 = summary([t["r"] for t in r2])
        ctrl = [summary([x for s, d in data.items() for x in control(stress(d), forex, per2[s], k)])["net"] for k in range(seeds)]
        beat = sum(s2["net"] > x for x in ctrl)
        ok = s2["trades"] >= 30 and s2["net"] > 0 and s2["pf"] >= 1.2 and beat >= round(0.9 * seeds)
        if label != "last 2 weeks":
            verdicts.append(ok)
        why = {w: sum(1 for t in r2 if t["why"] == w) for w in ("target", "stop", "session end", "day")}
        out.append(f"- {label}: 1x {fmt(summary([t['r'] for t in r1]))} | 2x {fmt(s2)} | longs {fmt(summary([t['r'] for t in r2 if t['side'] == 1]))} | "
                   f"shorts {fmt(summary([t['r'] for t in r2 if t['side'] == -1]))} | exits {why} | random controls median {np.median(ctrl):+.1f}R, "
                   f"beaten {beat}/{seeds}" + ("" if label == "last 2 weeks" else f" | {'pass' if ok else 'fail'}"))
    out.append(f"- {market} VERDICT: {'PASS (both windows)' if all(verdicts) else 'FAIL'}")
    return out


# ---- Sneaky Pivot as the range experts' buy / sell / take-profit guide (owner 2026-10-28: "use it in conjunction to help find buy, sell
# and TP to complement our range experts; retest 2 weeks") -----------------------------------------------------------------------------
# PRE-DECLARED. The live range experts (lien_h1, boxmr_h1 with the live 0.5R cut / 1.25R take-profit; retest_h1 with its own exits), only
# when the pair has no trend (the live range filter: no long trend, no hard move today), on 1-hour bars, 2x spread:
#   A  as live
#   B  BUY zone / SELL zone = the pivot: a long only if in the last 3 one-hour bars price traded at or below yesterday's low and closed back
#      above it (shorts: yesterday's high, closed back below) - the expert's own exits
#   C  B + the pivot's exits: stop 0.1 ATR beyond the lowest low (highest high) of those 3 bars, take profit at yesterday's OTHER level
#      (the far side of yesterday's range; needs at least 1R room), 48 bars at most (lien_h1 / boxmr_h1 only; retest keeps its structure)
# Control: B / C with the pivot readings moved to other days (20 seeds). Read on the last 2 weeks (owner) and the last 92 days (context).
def pivot_flags(m: pd.DataFrame, look: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(long_ok, short_ok, pdh, pdl) per 1-hour bar (forex day)."""
    key = day_key(m.index, True)
    pdh, pdl = prior_levels(m, key, True)
    l, h, c = (m[k].to_numpy(float) for k in ("low", "high", "close"))
    lt = (l <= pdl) & (c > pdl)
    st = (h >= pdh) & (c < pdh)
    lo = pd.Series(lt).rolling(look, min_periods=1).max().to_numpy(bool) & (c > pdl)
    so = pd.Series(st).rolling(look, min_periods=1).max().to_numpy(bool) & (c < pdh)
    return lo, so, pdh, pdl


class PivotRange:
    """A range expert behind the live range filter, with the pivot as buy / sell zone (exits='own') or zone + exits (exits='pivot')."""

    def __init__(self, base, exits: str = "own", flags_fn=None, use_pivot: bool = True):
        self.base, self.exits, self.flags_fn, self.use_pivot = base, exits, flags_fn or pivot_flags, use_pivot
        self.max_bars = 48 if exits == "pivot" else base.max_bars
        self.name = getattr(base, "name", type(base).__name__) + f" +pivot({exits})"

    def frame(self, m):
        from .strongtrend import daily_of, state_on_bars, today_state, votes
        f = self.base.frame(m).copy()
        d = daily_of(m)
        flat = (state_on_bars(m, votes(d)["long_state"]) == 0) & (today_state(m, d, True) == 0)
        lg = f["long"].fillna(False).astype(bool).to_numpy() & flat
        sh = f["short"].fillna(False).astype(bool).to_numpy() & flat
        if self.use_pivot:
            lo, so, pdh, pdl = self.flags_fn(m)
            lg, sh = lg & lo, sh & so
        f["long"], f["short"] = lg, sh
        if self.exits == "pivot":
            atr = _atr(m)
            c = m["close"].to_numpy(float)
            low3 = m["low"].rolling(3, min_periods=1).min().to_numpy(float)
            high3 = m["high"].rolling(3, min_periods=1).max().to_numpy(float)
            _, _, pdh, pdl = pivot_flags(m)
            sd_l, sd_s = c - (low3 - BUF_ATR * atr), (high3 + BUF_ATR * atr) - c
            tg_l, tg_s = pdh - c, c - pdl
            stop = np.where(lg, sd_l, np.where(sh, sd_s, f["stop"].to_numpy(float)))
            tgt = np.where(lg, tg_l, np.where(sh, tg_s, np.nan))
            room = np.where(lg | sh, tgt >= stop, True)
            f["long"], f["short"] = lg & room, sh & room
            f["stop"], f["target"] = stop, tgt
            f["exit_long"], f["exit_short"] = False, False
        return f


def range_pivot_report(h1: dict, today: date, seeds: int = 20) -> list[str]:
    from .engine import FIN_PER_NIGHT, simulate
    from .exitlab import _spread
    from .live import STRATS
    from .strategies import TightStop
    from .strongtrend import day_keys, permute_days, summary as ssum, fmt as sfmt
    a14, a92 = today - timedelta(days=14), today - timedelta(days=92)
    out = [f"## range_pivot: Sneaky Pivot as the range experts' buy / sell / TP guide (range filter on), 1-hour bars, 2x spread; "
           f"2 weeks {a14}..{today}, 92 days context"]

    def shuffled_flags(seed):
        def fn(m):
            lo, so, pdh, pdl = pivot_flags(m)
            k = day_keys(m.index, True)
            return permute_days(lo.astype(int), k, seed).astype(bool), permute_days(so.astype(int), k, seed).astype(bool), pdh, pdl
        return fn

    def run(name, variant, a, seed=None):
        rs = []
        for inst, df in h1.items():
            d2 = _spread(df, 2.0)
            if name == "retest_h1":
                from .data import mid
                from .playbook import PlaybookRules, simulate_playbook
                from .strongtrend import daily_of, state_on_bars, today_state, votes
                m = mid(df)
                dd = daily_of(m)
                flat = (state_on_bars(m, votes(dd)["long_state"]) == 0) & (today_state(m, dd, True) == 0)
                lo, so, _, _ = (shuffled_flags(seed) if seed is not None else pivot_flags)(m)
                pos = {t: i for i, t in enumerate(df.index)}
                for t in simulate_playbook(inst, d2, PlaybookRules(be_at_r=0.0), a, today):
                    k = pos.get(t["entry"])
                    if k is None or not flat[k - 1]:
                        continue
                    side = 1 if t["side"] == "long" else -1
                    if variant != "A" and not (lo[k - 1] if side == 1 else so[k - 1]):
                        continue
                    rs.append(t["r"])
                continue
            base = STRATS[name]["make"]()
            fl = shuffled_flags(seed) if seed is not None else None
            if variant == "A":
                strat = TightStop(PivotRange(base, "own", use_pivot=False), 0.50, 1.25)
            elif variant == "B":
                strat = TightStop(PivotRange(base, "own", fl), 0.50, 1.25)
            else:
                strat = PivotRange(base, "pivot", fl)
            for t in simulate(inst, d2, strat):
                if a <= t.entry_time.date() <= today:
                    rs.append(t.side * (t.exit_px - t.entry_px) / t.stop_dist - t.nights * FIN_PER_NIGHT * t.entry_px / t.stop_dist)
        return rs
    for name in ("lien_h1", "boxmr_h1", "retest_h1"):
        variants = ("A", "B") if name == "retest_h1" else ("A", "B", "C")
        for label, a in (("2 weeks", a14), ("92 days", a92)):
            row = []
            for v in variants:
                s = ssum(run(name, v, a))
                extra = ""
                if v != "A":
                    ctrl = [ssum(run(name, v, a, k))["net"] for k in range(seeds)]
                    extra = f" (shuffled-day pivot median {np.median(ctrl):+.1f}R, beaten {sum(s['net'] > x for x in ctrl)}/{seeds})"
                row.append(f"{v} {sfmt(s)}{extra}")
            out.append(f"- {name} {label}: " + " | ".join(row))
    return out


# ---- LEARN the profitable buy / sell / take-profit zones around yesterday's range (owner 2026-10-29: "I said hybrid with others to find
# buy, sell, TP profitable zones, or learn the best practice with this") ------------------------------------------------------------------
# PRE-DECLARED. Every live OANDA expert's own signals (range and trend families, no filters, so there is enough to learn from), 1-hour bars,
# 7 majors, 2x spread, the last 12 months: TRAIN = the first 9 months, TEST = the last 92 days (unseen) and the last 2 weeks (owner).
#  - Where a trade starts is read against yesterday's range: p = (entry - yesterday's low) / (yesterday's high - yesterday's low), in six
#    zones: below the low, 0-25%, 25-50%, 50-75%, 75-100%, above the high.
#  - BUY zones (longs) and SELL zones (shorts) = the zones with 15+ train trades and a positive average R, per family (range / trend).
#  - TAKE-PROFIT: per family, the best on train of {live 1.25R, 2R, 3R, the far side of yesterday's range (>= 1R)} inside the kept zones.
#  - TEST: only trades in the learned zones, with the learned take-profit. Control: the same number of zones picked at random (20 seeds).
#  - PASS (per family): test 92 days at 2x: 20+ trades, net R > 0, beats the as-live family and 18 of 20 random zone sets.
ZONES = ("below low", "0-25%", "25-50%", "50-75%", "75-100%", "above high")


def zone_of(p: float) -> str:
    if not np.isfinite(p):
        return ""
    return ZONES[0] if p < 0 else ZONES[5] if p > 1 else ZONES[1 + min(int(p * 4), 3)]


class FarTarget:
    """Take profit at the far side of yesterday's range (long: yesterday's high), the stop at the live 0.5R cut; skip if < 1R of room."""

    def __init__(self, base):
        self.base, self.max_bars = base, base.max_bars

    def frame(self, m):
        f = self.base.frame(m).copy()
        _, _, pdh, pdl = pivot_flags(m)
        c = m["close"].to_numpy(float)
        sd = f["stop"].to_numpy(float) * 0.5
        lg, sh = f["long"].fillna(False).to_numpy(bool), f["short"].fillna(False).to_numpy(bool)
        tg = np.where(lg, pdh - c, np.where(sh, c - pdl, np.nan))
        ok = np.isfinite(tg) & (tg >= sd)
        f["long"], f["short"], f["stop"], f["target"] = lg & ok, sh & ok, sd, tg
        return f


def zone_trades(h1: dict, names: list, tp: str, spread: float = 2.0) -> list[dict]:
    from .data import mid
    from .engine import FIN_PER_NIGHT, simulate
    from .exitlab import _spread
    from .live import STRATS
    from .strategies import TightStop
    out = []
    for inst, df in h1.items():
        m = mid(df)
        _, _, pdh, pdl = pivot_flags(m)
        pos = {t: i for i, t in enumerate(df.index)}
        d2 = _spread(df, spread)
        for name in names:
            if name == "retest_h1" or STRATS[name]["gran"] != "H1":
                continue
            base = STRATS[name]["make"]()
            strat = FarTarget(base) if tp == "far" else TightStop(base, 0.50, {"1.25R": 1.25, "2R": 2.0, "3R": 3.0}[tp])
            for t in simulate(inst, d2, strat):
                k = pos.get(t.entry_time)
                if k is None or k == 0:
                    continue
                rng = pdh[k - 1] - pdl[k - 1]
                p = (t.entry_px - pdl[k - 1]) / rng if rng > 0 else np.nan
                out.append({"name": name, "side": t.side, "date": t.entry_time.date(), "zone": zone_of(p),
                            "r": t.side * (t.exit_px - t.entry_px) / t.stop_dist - t.nights * FIN_PER_NIGHT * t.entry_px / t.stop_dist})
    return out


def zone_learn_report(h1: dict, today: date, seeds: int = 20) -> list[str]:
    from .live import RANGE_EXPERTS, TREND_EXPERTS
    from .strongtrend import fmt as sfmt, summary as ssum
    t_end = today - timedelta(days=92)
    tr_a = today - timedelta(days=365)
    a14 = today - timedelta(days=14)
    out = [f"## zone_learn: buy / sell / TP zones learned from yesterday's range, train {tr_a}..{t_end - timedelta(days=1)}, test {t_end}..{today} "
           f"(and the last 2 weeks), 2x spread"]
    fams = {"RANGE": [n for n in RANGE_EXPERTS], "TREND": [n for n in TREND_EXPERTS]}
    rng = np.random.default_rng(7)
    for fam, names in fams.items():
        by_tp = {tp: zone_trades(h1, names, tp) for tp in ("1.25R", "2R", "3R", "far")}
        live = by_tp["1.25R"]
        train = [t for t in live if tr_a <= t["date"] < t_end]
        keep = {}
        for side, lab in ((1, "BUY"), (-1, "SELL")):
            rows = []
            for z in ZONES:
                rs = [t["r"] for t in train if t["side"] == side and t["zone"] == z]
                s = ssum(rs)
                rows.append(f"{z} {s['trades']}t {s['net'] / max(s['trades'], 1):+.2f}R/t")
                if s["trades"] >= 15 and s["net"] > 0:
                    keep.setdefault(side, set()).add(z)
            out.append(f"- {fam} {lab} zones on train: " + " | ".join(rows) + f" -> kept {sorted(keep.get(side, set())) or 'none'}")

        def inzone(t):
            return t["zone"] in keep.get(t["side"], set())
        best_tp, best = "1.25R", None
        for tp, ts in by_tp.items():
            v = sum(t["r"] for t in ts if tr_a <= t["date"] < t_end and inzone(t))
            out.append(f"  {fam} TP {tp}: train in-zone net {v:+.1f}R")
            if best is None or v > best:
                best, best_tp = v, tp
        out.append(f"  {fam} learned TP: {best_tp}")
        n_keep = sum(len(v) for v in keep.values())
        for label, a in (("TEST 92 days", t_end), ("last 2 weeks", a14)):
            asl = ssum([t["r"] for t in live if t["date"] >= a])
            lrn = ssum([t["r"] for t in by_tp[best_tp] if t["date"] >= a and inzone(t)])
            ctrl = []
            for _ in range(seeds):
                pick = {1: set(), -1: set()}
                choices = [(s_, z) for s_ in (1, -1) for z in ZONES]
                for i in rng.choice(len(choices), size=min(n_keep, len(choices)), replace=False):
                    pick[choices[i][0]].add(choices[i][1])
                ctrl.append(sum(t["r"] for t in by_tp[best_tp] if t["date"] >= a and t["zone"] in pick[t["side"]]))
            beat = sum(lrn["net"] > x for x in ctrl)
            ok = lrn["trades"] >= 20 and lrn["net"] > 0 and lrn["net"] > asl["net"] and beat >= round(0.9 * seeds)
            out.append(f"- {fam} {label}: as live {sfmt(asl)} -> learned zones + {best_tp} {sfmt(lrn)} | random zone sets median "
                       f"{np.median(ctrl):+.1f}R, beaten {beat}/{seeds}" + (f" | {'PASS' if ok else 'fail'}" if label.startswith("TEST") else ""))
    return out
