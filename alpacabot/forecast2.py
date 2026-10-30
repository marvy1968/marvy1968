"""Forecast v2 = Gemini round 9 applied (owner 2026-10-29). v1 (alpacabot.forecast) learned market beta and lost (day PF 0.69, week PF 0.54).

PRE-DECLARED before any run:
NEW FEATURES (on top of v1's): overnight-vs-intraday tug of war (5-day sum of open/prev close minus 5-day sum of close/open, vol-scaled),
volume-signed shock (1-day return x volume / 20-day average volume, vol-scaled), rank of the 20-day return inside its own sector (0..1).

A  RELATIVE WEEK: target = next-5-day return MINUS SPY's, in the stock's vol units. Every 5th session, while SPY is above its 200-day, buy the 5
   stocks with the highest predicted relative return at the next open; stop = the 10-day low (Gemini), moved to entry + 10 bps once price covers
   half the way to the target; target = entry + 1.5 sigma5; out at the 5th close.  Controls: 5 RANDOM stocks on the same days with the same
   exits (20 seeds) and SPY bought on the same days with the same exits.
B  RANGE FADE (day, long only, SPY above its 200-day): quantile models of tomorrow's low / high / close relative to today's close.
   B1 (Gemini Q3): limit buy at the predicted 10th-percentile low, stop at the 2nd-percentile low, take profit at the predicted median close.
   B2 (Gemini Q4): limit buy at the 25th-percentile low, stop at the 5th-percentile low, take profit at the 75th-percentile high.
   Skip when TP - entry < 0.3%. Out at the close. Control: the same rule with each stock's plain last-250-day quantiles (no model).
TEST: walk-forward over the last 252 sessions, refit every 21 (5-day embargo), 10 bps a side, stop first when a bar touches both.
USABLE: 100+ trades, net R > 0, PF >= 1.1, net > 0 in each half of the year, and better than its controls (A: beats 18/20 random-stock runs
AND the SPY run; B: beats the no-model quantile version).
"""

from datetime import date

import numpy as np
import pandas as pd

from . import forecast as v1

COST = v1.COST
FEATS2 = v1.FEATS + ["onid", "vshock", "sec_rank"]
QS = {"lo02": ("ql", 0.02), "lo05": ("ql", 0.05), "lo10": ("ql", 0.10), "lo25": ("ql", 0.25), "hi75": ("qh", 0.75), "c50": ("y1", 0.50)}
B_RULES = {"B1": ("lo10", "lo02", "c50"), "B2": ("lo25", "lo05", "hi75")}
TOP_A = 5


def panel2(daily: dict, sector_of: dict) -> pd.DataFrame:
    spy = daily["SPY"]
    out = []
    for s, d in daily.items():
        if len(d) < 300:
            continue
        f = v1.features(d, spy, daily.get(sector_of.get(s)))
        o, h, l, c = (d[k].astype(float) for k in ("open", "high", "low", "close"))
        v = d["volume"].astype(float) if "volume" in d else pd.Series(1.0, index=d.index)
        on, idr = np.log(o / c.shift(1)), np.log(c / o)
        f["onid"] = (on.rolling(5).sum() - idr.rolling(5).sum()) / (f["vol"] * np.sqrt(5))
        f["vshock"] = np.log(c / c.shift(1)) * (v / v.rolling(20).mean()) / f["vol"]
        f["r20raw"] = np.log(c / c.shift(20))
        f["sector"] = sector_of.get(s, "")
        f["symbol"] = s
        f["o"], f["h"], f["l"] = o, h, l
        f["ql"] = np.log(l.shift(-1) / c) / f["vol"]
        f["qh"] = np.log(h.shift(-1) / c) / f["vol"]
        spy_c = spy["close"].reindex(d.index).ffill()
        f["y5rel"] = (np.log(c.shift(-5) / c) - np.log(spy_c.shift(-5) / spy_c)) / (f["vol"] * np.sqrt(5))
        out.append(f)
    p = pd.concat(out)
    p["sec_rank"] = 0.5
    m = p["sector"] != ""
    p.loc[m, "sec_rank"] = p[m].groupby([p[m].index, p[m]["sector"]])["r20raw"].rank(pct=True)
    return p.sort_index()


def _fit_rel(tr: pd.DataFrame):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    tr = tr[tr["sector"] != ""].dropna(subset=FEATS2 + ["y5rel"])
    y = tr["y5rel"].clip(-4, 4)
    r = Ridge(alpha=10.0).fit(tr[FEATS2], y)
    g = HistGradientBoostingRegressor(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=200, l2_regularization=1.0,
                                      random_state=0).fit(tr[FEATS2], y)
    return lambda z: 0.5 * r.predict(z[FEATS2]) + 0.5 * g.predict(z[FEATS2])


def _fit_q(tr: pd.DataFrame) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    ms = {}
    for name, (col, a) in QS.items():
        t = tr.dropna(subset=FEATS2 + [col])
        ms[name] = HistGradientBoostingRegressor(loss="quantile", quantile=a, max_depth=3, max_iter=120, learning_rate=0.05,
                                                 min_samples_leaf=300, random_state=0).fit(t[FEATS2], t[col].clip(-8, 8))
    return ms


def walk(p: pd.DataFrame, days_test: int = 252, every: int = 21) -> pd.DataFrame:
    days = np.sort(p.index.unique())
    test_days = days[-days_test - 1:-1]
    out = []
    for k in range(0, len(test_days), every):
        block = test_days[k:k + every]
        hist = p[p.index < days[np.searchsorted(days, block[0]) - 5]]
        fr, fq = _fit_rel(hist), _fit_q(hist)
        z = p[p.index.isin(block)].dropna(subset=FEATS2).copy()
        z["rel"] = fr(z)
        for name, m in fq.items():
            z[name] = m.predict(z[FEATS2])
        out.append(z)
    return pd.concat(out)


def _arrays(p: pd.DataFrame) -> dict:
    return {s: ({t: i for i, t in enumerate(g.index)}, *(g[k].to_numpy(float) for k in ("o", "h", "l", "close")))
            for s, g in p.groupby("symbol")}


def week_trade(arr, s, t, sig5) -> float | None:
    pos, o, h, l, c = arr[s]
    i = pos.get(t)
    if i is None or i + 5 >= len(o) or i < 10:
        return None
    entry = o[i + 1] * (1 + COST)
    stop = l[i - 9:i + 1].min()
    if stop >= entry:
        return None
    risk, tgt = entry - stop, entry + 1.5 * sig5
    be = False
    for k in range(i + 1, i + 6):
        if l[k] <= stop:
            px = o[k] if k > i + 1 and o[k] <= stop else stop
            return (px * (1 - COST) - entry) / risk
        if h[k] >= tgt:
            px = o[k] if k > i + 1 and o[k] >= tgt else tgt
            return (px * (1 - COST) - entry) / risk
        if not be and h[k] >= entry + 0.5 * (tgt - entry):
            stop, be = entry * (1 + COST), True
    return (c[i + 5] * (1 - COST) - entry) / risk


def eval_a(wf: pd.DataFrame, arr: dict, spy200: dict, seeds: int = 20) -> tuple[str, bool]:
    days = pd.DatetimeIndex(np.sort(wf.index.unique()))[::5]
    mid = days[len(days) // 2]
    real, spy_r, halves = [], [], ([], [])
    ctrl = [[] for _ in range(seeds)]
    rngs = [np.random.default_rng(k) for k in range(seeds)]
    for t in days:
        if spy200.get(t, 0) <= 0:
            continue
        g = wf.loc[[t]]
        g = g[g["sector"] != ""]
        top = g.sort_values("rel").tail(TOP_A)
        for _, r in top.iterrows():
            x = week_trade(arr, r["symbol"], t, r["vol"] * r["close"] * np.sqrt(5))
            if x is not None:
                real.append(x)
                (halves[0] if t < mid else halves[1]).append(x)
        spr = wf.loc[[t]]
        spr = spr[spr["symbol"] == "SPY"]
        if len(spr):
            x = week_trade(arr, "SPY", t, spr["vol"].iloc[0] * spr["close"].iloc[0] * np.sqrt(5))
            if x is not None:
                spy_r += [x] * len(top)
        names = list(g["symbol"])
        for k in range(seeds):
            for s in rngs[k].choice(names, size=min(TOP_A, len(names)), replace=False):
                r = g[g["symbol"] == s].iloc[0]
                x = week_trade(arr, s, t, r["vol"] * r["close"] * np.sqrt(5))
                if x is not None:
                    ctrl[k].append(x)
    s, sp = v1.summary(real), v1.summary(spy_r)
    h0, h1 = v1.summary(halves[0]), v1.summary(halves[1])
    cn = [v1.summary(c)["net"] for c in ctrl]
    beat = sum(s["net"] > x for x in cn)
    ok = (s["trades"] >= 100 and s["net"] > 0 and s["pf"] >= 1.1 and h0["net"] > 0 and h1["net"] > 0 and beat >= round(0.9 * seeds)
          and s["net"] > sp["net"])
    return (f"- A RELATIVE WEEK (top {TOP_A} by predicted return vs SPY, SPY > 200-day): {v1.fmt(s)} | halves {v1.fmt(h0)} / {v1.fmt(h1)} | random stocks "
            f"median {np.median(cn):+.1f}R, beaten {beat}/{seeds} | SPY same days {v1.fmt(sp)} | {'USABLE' if ok else 'NOT usable'}"), ok


def day_fade(arr, s, t, close, vol, q: dict, rule) -> float | None:
    e_k, s_k, t_k = rule
    entry, stop, tgt = (close * np.exp(q[k] * vol) for k in (e_k, s_k, t_k))
    if not (stop < entry < tgt) or (tgt - entry) / entry < 0.003:
        return None
    pos, o, h, l, c = arr[s]
    i = pos.get(t)
    if i is None or i + 1 >= len(o):
        return None
    k = i + 1
    if o[k] <= entry:
        entry = o[k]
        if entry <= stop:
            return None
    elif l[k] > entry:
        return None
    risk = entry - stop
    if l[k] <= stop:
        px = stop
    elif h[k] >= tgt:
        px = tgt
    else:
        px = c[k]
    return (px * (1 - COST) - entry * (1 + COST)) / risk


def eval_b(wf: pd.DataFrame, p: pd.DataFrame, arr: dict, spy200: dict) -> tuple[list[str], dict]:
    days = np.sort(wf.index.unique())
    mid = days[len(days) // 2]
    # no-model control: each symbol's plain last-250-session quantiles as of the day
    base = {}
    for s, g in p.groupby("symbol"):
        qq = {}
        for name, (col, a) in QS.items():
            qq[name] = g[col].shift(1).rolling(250, min_periods=120).quantile(a)
        base[s] = pd.DataFrame(qq)
    out, ok_all = [], {}
    for rn, rule in B_RULES.items():
        real, ctl, halves = [], [], ([], [])
        for t, r in wf.iterrows():
            if spy200.get(t, 0) <= 0 or r["sector"] == "":
                continue
            x = day_fade(arr, r["symbol"], t, r["close"], r["vol"], r, rule)
            if x is not None:
                real.append(x)
                (halves[0] if t < mid else halves[1]).append(x)
            b = base[r["symbol"]]
            if t in b.index and b.loc[t].notna().all():
                y = day_fade(arr, r["symbol"], t, r["close"], r["vol"], b.loc[t], rule)
                if y is not None:
                    ctl.append(y)
        s, c = v1.summary(real), v1.summary(ctl)
        h0, h1 = v1.summary(halves[0]), v1.summary(halves[1])
        ok = s["trades"] >= 100 and s["net"] > 0 and s["pf"] >= 1.1 and h0["net"] > 0 and h1["net"] > 0 and s["net"] > c["net"]
        ok_all[rn] = ok
        e_k, s_k, t_k = rule
        out.append(f"- {rn} RANGE FADE (buy {e_k}, stop {s_k}, TP {t_k}): {v1.fmt(s)} | halves {v1.fmt(h0)} / {v1.fmt(h1)} | no-model quantiles "
                   f"{v1.fmt(c)} | {'USABLE' if ok else 'NOT usable'}")
    return out, ok_all


def report(daily_raw: dict, sector_of: dict, today: date, state_dir=None, five: dict | None = None) -> list[str]:
    daily = {s: v1._dates(d) for s, d in daily_raw.items() if len(d)}
    daily = {s: d[d.index < pd.Timestamp(today)] for s, d in daily.items()}
    p = panel2(daily, sector_of)
    sc = daily["SPY"]["close"]
    spy200 = ((sc > sc.rolling(200).mean()).astype(int)).to_dict()
    wf = walk(p)
    arr = _arrays(p)
    out = [f"## forecast v2 (Gemini round 9): walk-forward last 252 sessions to {p.index.max().date()}, {p['symbol'].nunique()} symbols, 10 bps a side"]
    a_line, a_ok = eval_a(wf, arr, spy200)
    out.append(a_line)
    b_lines, b_ok = eval_b(wf, p, arr, spy200)
    out += b_lines
    out += sizing_report(wf, arr, spy200)
    if five:
        out += check_intraday(wf, p, arr, spy200, five, min(pd.Timestamp(d.index.min()).tz_convert('America/New_York').date() for d in five.values() if len(d)))
    # today's plan from models fitted on everything up to the last close
    days = np.sort(p.index.unique())
    last = pd.Timestamp(days[-1])
    fr, fq = _fit_rel(p[p.index <= days[-6]]), _fit_q(p[p.index <= days[-2]])
    z = p[p.index == last].dropna(subset=FEATS2).copy()
    z["rel"] = fr(z)
    for name, m in fq.items():
        z[name] = m.predict(z[FEATS2])
    up = spy200.get(last, 0) > 0
    out.append(f"## plan from the {last.date()} close (SPY above its 200-day: {'yes' if up else 'NO - no new longs'})")
    top = z[z["sector"] != ""].sort_values("rel", ascending=False)
    out.append("A week (top 5 vs SPY): " + ", ".join(f"{r.symbol} {r.rel:+.2f}" for r in top.head(TOP_A).itertuples())
               + (" -> BUY at the open, stop = 10-day low" if a_ok and up else " (not usable: watch only)"))
    plan = []
    for r in top.itertuples():
        row = {"symbol": r.symbol, "close": r.close, "rel_week": r.rel}
        for rn, (e_k, s_k, t_k) in B_RULES.items():
            e, s_, t_ = (r.close * np.exp(getattr(r, k) * r.vol) for k in (e_k, s_k, t_k))
            row.update({f"{rn}_buy": e, f"{rn}_stop": s_, f"{rn}_tp": t_, f"{rn}_live": bool(b_ok.get(rn) and up and s_ < e < t_ and (t_ - e) / e >= 0.003)})
        plan.append(row)
    pl = pd.DataFrame(plan).set_index("symbol")
    for s, r in pl.iterrows():
        out.append(f"  {s:5s} {r.close:9.2f} | week vs SPY {r.rel_week:+.2f} | B1 buy {r.B1_buy:.2f} stop {r.B1_stop:.2f} tp {r.B1_tp:.2f}"
                   f"{' LIVE' if r.B1_live else ''} | B2 buy {r.B2_buy:.2f} stop {r.B2_stop:.2f} tp {r.B2_tp:.2f}{' LIVE' if r.B2_live else ''}")
    if state_dir is not None:
        from pathlib import Path
        sd = Path(state_dir)
        sd.mkdir(parents=True, exist_ok=True)
        pl.to_json(sd / f"plan2-{today:%Y%m%d}.json", orient="index", indent=1)
    return out


# ---- intraday order check (added after the first run: daily bars cannot tell whether the day's high came BEFORE the dip that filled the
# buy, which can count a take-profit that happened before the entry). PRE-DECLARED: the same B1 levels (model and no-model), the next
# session's 5-minute bars walked in time order: fill when a bar trades at/below the buy (a bar opening below fills at its open, skipped if
# that is already below the stop); stop checked from the fill bar on, take-profit only from the bar AFTER the fill; else out at the last bar.
def intraday_fade(bars5: pd.DataFrame, entry: float, stop: float, tgt: float) -> float | None:
    o, h, l, c = (bars5[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    for k in range(len(o)):
        if o[k] <= entry or l[k] <= entry:
            e = min(o[k], entry)
            if e <= stop:
                return None
            for j in range(k, len(o)):
                if l[j] <= stop:
                    px = o[j] if j > k and o[j] <= stop else stop
                    return (px * (1 - COST) - e * (1 + COST)) / (e - stop)
                if j > k and h[j] >= tgt:
                    px = o[j] if o[j] >= tgt else tgt
                    return (px * (1 - COST) - e * (1 + COST)) / (e - stop)
            return (c[-1] * (1 - COST) - e * (1 + COST)) / (e - stop)
    return None


def check_intraday(wf: pd.DataFrame, p: pd.DataFrame, arr: dict, spy200: dict, five: dict, since) -> list[str]:
    rule = B_RULES["B1"]
    base = {}
    for s, g in p.groupby("symbol"):
        base[s] = pd.DataFrame({n: g[col].shift(1).rolling(250, min_periods=120).quantile(a) for n, (col, a) in QS.items()})
    sess = {}
    for s, m in five.items():
        x = m.copy()
        x.index = x.index.tz_convert("America/New_York")
        x = x[(x.index.time >= pd.Timestamp("09:30").time()) & (x.index.time < pd.Timestamp("16:00").time())]
        sess[s] = {pd.Timestamp(d): g for d, g in x.groupby(x.index.date)}
    dates = {s: list(g.index) for s, g in p.groupby("symbol")}
    res = {k: ([], []) for k in ("model", "no-model")}
    for t, r in wf[wf.index >= pd.Timestamp(since)].iterrows():
        s = r["symbol"]
        if spy200.get(t, 0) <= 0 or r["sector"] == "" or s not in sess:
            continue
        pos = arr[s][0].get(t)
        if pos is None or pos + 1 >= len(dates[s]):
            continue
        nxt = dates[s][pos + 1]
        if nxt not in sess[s]:
            continue
        for kind, q in (("model", r), ("no-model", base[s].loc[t] if t in base[s].index else None)):
            if q is None or not np.all(np.isfinite([q[k] for k in rule])):
                continue
            e, st, tg = (r["close"] * np.exp(q[k] * r["vol"]) for k in rule)
            if not (st < e < tg) or (tg - e) / e < 0.003:
                continue
            d = day_fade(arr, s, t, r["close"], r["vol"], q, rule)
            i = intraday_fade(sess[s][nxt], e, st, tg)
            if d is not None and i is not None:
                res[kind][0].append(d)
                res[kind][1].append(i)
    out = [f"## B1 intraday order check (5-minute bars, sessions since {since}): same trades, daily-bar result vs time-ordered result"]
    for kind, (d, i) in res.items():
        out.append(f"- {kind}: daily bars {v1.fmt(v1.summary(d))} -> 5-minute order {v1.fmt(v1.summary(i))}")
    return out


# ---- dynamic sizing + early loss cuts (owner 2026-10-30: "try aggressive dynamic sizing, cutting losers early") ------------------------------
# PRE-DECLARED, on the A (relative week) trades of the same walk-forward year, entries unchanged:
#  EXITS  base = 10-day-low stop as live | cut05 = stop at entry - 0.5 x (entry - 10-day low) | day2 = out at the 2nd close if under the entry
#  SIZING (risk per trade as % of equity, compounding, trades in date order; same-day trades sized from the same equity)
#    fixed 0.5% | fixed 1% | streak = 0.5% x (1 + wins in a row), max 2% | equity-curve = 1% while the last 10 trades sum > 0 R, else 0.25%
#    half-Kelly = 0.5 x mean / variance of the last 30 trades' R (0.25%..3%)
#  Read: final return and worst drawdown over the year; nothing is adopted unless it beats fixed 0.5% on return/drawdown in BOTH halves.
def week_trade_x(arr, s, t, sig5, exit_rule: str) -> float | None:
    pos, o, h, l, c = arr[s]
    i = pos.get(t)
    if i is None or i + 5 >= len(o) or i < 10:
        return None
    entry = o[i + 1] * (1 + COST)
    stop0 = l[i - 9:i + 1].min()
    if stop0 >= entry:
        return None
    risk = entry - stop0
    stop = entry - 0.5 * risk if exit_rule == "cut05" else stop0
    tgt = entry + 1.5 * sig5
    be = False
    for k in range(i + 1, i + 6):
        if l[k] <= stop:
            px = o[k] if k > i + 1 and o[k] <= stop else stop
            return (px * (1 - COST) - entry) / risk
        if h[k] >= tgt:
            px = o[k] if k > i + 1 and o[k] >= tgt else tgt
            return (px * (1 - COST) - entry) / risk
        if exit_rule == "day2" and k == i + 2 and c[k] < entry:
            return (c[k] * (1 - COST) - entry) / risk
        if not be and h[k] >= entry + 0.5 * (tgt - entry):
            stop, be = max(stop, entry * (1 + COST)), True
    return (c[i + 5] * (1 - COST) - entry) / risk


def _equity(trades: list, scheme: str) -> tuple[float, float]:
    eq, peak, dd, hist = 1.0, 1.0, 0.0, []
    for _, day in pd.Series(trades).groupby([t[0] for t in trades]):
        rs = [t[1] for t in day]
        if scheme == "fixed05":
            f = 0.005
        elif scheme == "fixed1":
            f = 0.01
        elif scheme == "streak":
            n = 0
            for x in reversed(hist):
                if x <= 0:
                    break
                n += 1
            f = min(0.02, 0.005 * (1 + n))
        elif scheme == "equity":
            f = 0.01 if len(hist) < 10 or sum(hist[-10:]) > 0 else 0.0025
        else:                                                   # half-Kelly: 0.5 x mean / variance of the last 30 trades' R
            w = np.asarray(hist[-30:], float)
            f = 0.005 if len(w) < 10 or w.var() == 0 else float(np.clip(0.5 * w.mean() / w.var(), 0.0025, 0.03))
        eq *= 1 + f * sum(rs)
        hist += rs
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return eq - 1, dd


def sizing_report(wf: pd.DataFrame, arr: dict, spy200: dict) -> list[str]:
    days = pd.DatetimeIndex(np.sort(wf.index.unique()))[::5]
    mid = days[len(days) // 2]
    out = ["## sizing_lab (A relative-week trades, last year): exit rule x sizing scheme -> return / worst drawdown (whole year | 1st half | 2nd half)"]
    for ex in ("base", "cut05", "day2"):
        trades = []
        for t in days:
            if spy200.get(t, 0) <= 0:
                continue
            g = wf.loc[[t]]
            g = g[g["sector"] != ""]
            for r in g.sort_values("rel").tail(TOP_A).itertuples():
                x = week_trade_x(arr, r.symbol, t, r.vol * r.close * np.sqrt(5), ex)
                if x is not None:
                    trades.append((t, x))
        s = v1.summary([x for _, x in trades])
        out.append(f"- exit {ex}: {v1.fmt(s)}")
        for sc in ("fixed05", "fixed1", "streak", "equity", "halfkelly"):
            parts = []
            for seg in (trades, [t for t in trades if t[0] < mid], [t for t in trades if t[0] >= mid]):
                ret, dd = _equity(seg, sc) if seg else (0.0, 0.0)
                parts.append(f"{ret:+.1%} / {dd:.1%}")
            out.append(f"    {sc:9s} " + " | ".join(parts))
    return out
