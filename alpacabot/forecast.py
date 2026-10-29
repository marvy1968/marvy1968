"""Behaviour + forecast + levels for every sector ETF and stock we trade (owner 2026-10-29: "how has each sector and stock behaved over the past
year, design an algorithm to predict where it's going today and in seven days, then place our stops, buys and TP for each").

1) BEHAVIOUR (last 252 trading days, daily bars): return, volatility, max drawdown, days above the 200-day, beta to SPY, average daily range,
   character (lag-1 autocorrelation: < 0 = snaps back, > 0 = follows through), up-day share, return vs its sector ETF.
2) FORECAST: one pooled model over all symbols. Features as of the last close (vol-scaled returns over 1/5/20/60/120/250 days, distance from the
   20/50/200-day averages in ATRs, RSI(2) and RSI(14), 20- vs 60-day volatility, 20-day strength vs its sector ETF and SPY, SPY above its
   200-day). Targets: next-day and next-5-day (= 7 calendar days) return in volatility units. Ridge + small gradient boosting, averaged, then a
   logistic calibration fitted on the most recent 20% of the training window -> P(up). Ranges come from EWMA volatility (68% band = 1 sigma).
3) LEVELS, PRE-DECLARED (no tuning on the test year):
   Calls are CROSS-SECTIONAL: each day the 5 symbols with the highest P(up) (if above 0.5) are longs and the 5 lowest (if below 0.5) shorts.
   DAY  (next session): BUY limit = last close - 0.3 sigma1 (open below it fills at the open),
        STOP = entry - 1.0 sigma1, TP = entry + 1.5 sigma1, else out at the close. Short mirrored.
   WEEK (5 sessions, ranked on P(up 5d)): BUY limit = last close - 0.5 sigma5 within the first 2 sessions,
        STOP = entry - 1.0 sigma5, TP = entry + 1.5 sigma5, else out at the 5th close.  sigma1 = daily EWMA vol x price, sigma5 = sigma1 x sqrt(5).
   Stop before target when one bar touches both. 10 bps a side.
TEST: walk-forward over the last 252 trading days, refit every 21 days on everything before (5-day embargo). Controls: the same levels with a
random side (20 seeds), random NAMES with the same long/short count (20 seeds), and the same names always long. Weekly calls only every 5th\nsession (no overlapping trades). A horizon is USABLE only if it has 100+ trades, net R > 0, PF >= 1.1, beats 18/20 of BOTH random controls, is net > 0 with PF >= 1.1 in EACH half of the year AND
beats always-long. Otherwise its levels are shown as ranges only (no buy/sell call).
"""

from datetime import date

import numpy as np
import pandas as pd

TOP_K = 5                                                   # calls per day: the 5 highest P(up) long, the 5 lowest short
DAY_LVL, WEEK_LVL = (0.3, 1.0, 1.5), (0.5, 1.0, 1.5)       # (pullback, stop, target) in sigmas
COST = 10.0 / 1e4
FEATS = ["r1", "r5", "r20", "r60", "r120", "r250", "d20", "d50", "d200", "rsi2", "rsi14", "vr", "rs_sec", "rs_spy", "spy200"]


def _dates(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    idx = pd.DatetimeIndex(d.index)
    d.index = pd.to_datetime((idx.tz_convert("America/New_York") if idx.tz is not None else idx).date)
    return d[~d.index.duplicated(keep="last")].sort_index()


def _rsi(c: pd.Series, n: int) -> pd.Series:
    ch = c.diff()
    up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(d: pd.DataFrame, spy: pd.DataFrame, sec: pd.DataFrame | None) -> pd.DataFrame:
    c = d["close"].astype(float)
    lr = np.log(c).diff()
    vol = lr.ewm(span=20, adjust=False).std()
    pc = c.shift(1)
    atr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean()
    f = pd.DataFrame(index=d.index)
    for k in (1, 5, 20, 60, 120, 250):
        f[f"r{k}"] = np.log(c / c.shift(k)) / (vol * np.sqrt(k))
    for k in (20, 50, 200):
        f[f"d{k}"] = (c - c.rolling(k).mean()) / atr
    f["rsi2"], f["rsi14"] = (_rsi(c, 2) - 50) / 50, (_rsi(c, 14) - 50) / 50
    f["vr"] = np.log(vol / lr.rolling(60).std())
    sc = spy["close"].reindex(d.index).ffill()
    r20 = np.log(c / c.shift(20))
    f["rs_spy"] = (r20 - np.log(sc / sc.shift(20))) / (vol * np.sqrt(20))
    if sec is not None:
        ec = sec["close"].reindex(d.index).ffill()
        f["rs_sec"] = (r20 - np.log(ec / ec.shift(20))) / (vol * np.sqrt(20))
    else:
        f["rs_sec"] = 0.0
    f["spy200"] = (sc > sc.rolling(200).mean()).astype(float) * 2 - 1
    f["vol"], f["close"] = vol, c
    f["y1"] = lr.shift(-1) / vol
    f["y5"] = np.log(c.shift(-5) / c) / (vol * np.sqrt(5))
    return f


def behaviour(daily: dict, sector_of: dict) -> pd.DataFrame:
    spy = daily["SPY"]["close"].iloc[-253:]
    sr = np.log(spy).diff().dropna()
    rows = []
    for s, d in daily.items():
        if len(d) < 260:
            continue
        c = d["close"].astype(float)
        y = c.iloc[-253:]
        lr = np.log(y).diff().dropna()
        dd = (y / y.cummax() - 1).min()
        sma200 = c.rolling(200).mean().iloc[-252:]
        al = lr.align(sr, join="inner")
        beta = np.cov(al[0], al[1])[0, 1] / np.var(al[1]) if len(al[0]) > 50 else np.nan
        rng = ((d["high"] - d["low"]) / d["close"]).iloc[-252:].mean()
        sec = sector_of.get(s)
        vs = np.nan
        if sec and sec in daily and len(daily[sec]) >= 253:
            vs = y.iloc[-1] / y.iloc[0] - daily[sec]["close"].iloc[-1] / daily[sec]["close"].iloc[-253]
        rows.append({"symbol": s, "sector": sec or ("ETF" if s.startswith("XL") else "index"), "ret_1y": y.iloc[-1] / y.iloc[0] - 1,
                     "vol": lr.std() * np.sqrt(252), "max_dd": dd, "above200": float((y.iloc[-252:] > sma200).mean()), "beta": beta,
                     "day_range": rng, "ac": lr.autocorr(1), "up_days": float((lr > 0).mean()), "vs_sector": vs,
                     "ret_3m": y.iloc[-1] / y.iloc[-64] - 1})
    return pd.DataFrame(rows).set_index("symbol")


def panel(daily: dict, sector_of: dict) -> pd.DataFrame:
    spy = daily["SPY"]
    out = []
    for s, d in daily.items():
        if len(d) < 300:
            continue
        f = features(d, spy, daily.get(sector_of.get(s)))
        f["symbol"] = s
        f["o"], f["h"], f["l"] = d["open"].astype(float), d["high"].astype(float), d["low"].astype(float)
        out.append(f)
    return pd.concat(out).sort_index()


def _fit(tr: pd.DataFrame, target: str):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    tr = tr.dropna(subset=FEATS + [target])
    days = np.sort(tr.index.unique())
    cut = days[int(len(days) * 0.8)]
    a, b = tr[tr.index < cut], tr[tr.index >= cut]
    ya = a[target].clip(-4, 4)

    def models(x, y):
        r = Ridge(alpha=10.0).fit(x[FEATS], y)
        g = HistGradientBoostingRegressor(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=200, l2_regularization=1.0,
                                          random_state=0).fit(x[FEATS], y)
        return lambda z: 0.5 * r.predict(z[FEATS]) + 0.5 * g.predict(z[FEATS])
    m_a = models(a, ya)
    cal = LogisticRegression(C=1.0).fit(m_a(b).reshape(-1, 1), (b[target] > 0).astype(int))
    m = models(tr, tr[target].clip(-4, 4))
    return lambda z: (m(z), cal.predict_proba(m(z).reshape(-1, 1))[:, 1])


def walk_forward(p: pd.DataFrame, days_test: int = 252, every: int = 21) -> pd.DataFrame:
    days = np.sort(p.index.unique())
    test_days = days[-days_test - 1:-1]                       # the last day has no next-day outcome yet
    out = []
    for k in range(0, len(test_days), every):
        block = test_days[k:k + every]
        hist = p[p.index < days[np.searchsorted(days, block[0]) - 5]]
        f1, f5 = _fit(hist, "y1"), _fit(hist, "y5")
        z = p[p.index.isin(block)].dropna(subset=FEATS)
        z = z.assign(s1=f1(z)[0], p1=f1(z)[1], s5=f5(z)[0], p5=f5(z)[1])
        out.append(z)
    return pd.concat(out)


def _trade(o, h, l, c, side: int, last_close: float, sig: float, lvl: tuple, fill_days: int) -> float | None:
    pull, stop_k, tgt_k = lvl
    limit = last_close - side * pull * sig
    entry, k0 = None, None
    for k in range(min(fill_days, len(o))):
        if (side == 1 and o[k] <= limit) or (side == -1 and o[k] >= limit):
            entry, k0 = o[k], k
        elif (side == 1 and l[k] <= limit) or (side == -1 and h[k] >= limit):
            entry, k0 = limit, k
        if entry is not None:
            break
    if entry is None:
        return None
    sd = stop_k * sig
    stop, tgt = entry - side * sd, entry + side * tgt_k * sig
    px = None
    for k in range(k0, len(o)):
        if (side == 1 and l[k] <= stop) or (side == -1 and h[k] >= stop):
            px = o[k] if k > k0 and side * (o[k] - stop) <= 0 else stop
        elif (side == 1 and h[k] >= tgt) or (side == -1 and l[k] <= tgt):
            px = o[k] if k > k0 and side * (o[k] - tgt) >= 0 else tgt
        if px is not None:
            break
    if px is None:
        px = c[-1]
    return side * (px - entry) / sd - 2 * COST * entry / sd


def _prep(wf: pd.DataFrame, p: pd.DataFrame) -> dict:
    by = {}
    for s, g in p.groupby("symbol"):
        by[s] = ({t: i for i, t in enumerate(g.index)}, *(g[k].to_numpy(float) for k in ("o", "h", "l", "close")))
    rows = list(zip(wf.index, wf["symbol"], wf["p1"], wf["p5"], wf["vol"], wf["close"]))
    return {"by": by, "rows": rows}


def simulate(pre: dict, horizon: str, side_fn) -> list[float]:
    n, fill_days, lvl = (1, 1, DAY_LVL) if horizon == "day" else (5, 2, WEEK_LVL)
    rs = []
    for t, s, p1, p5, vol, close in pre["rows"]:
        side = side_fn(p1 if horizon == "day" else p5, t, s)
        if not side:
            continue
        pos, o, h, l, c = pre["by"][s]
        i = pos[t]
        sl = slice(i + 1, i + 1 + n)
        if len(o[sl]) < n:
            continue
        sig = vol * close * (1 if n == 1 else np.sqrt(5))
        r = _trade(o[sl], h[sl], l[sl], c[sl], side, close, sig, lvl, fill_days)
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


def calls(df: pd.DataFrame, pcol: str, k: int = TOP_K) -> dict:
    """{(date, symbol): side}: each day the k highest P(up) above 0.5 are longs, the k lowest below 0.5 are shorts."""
    out = {}
    for t, g in df.groupby(level=0):
        g = g.sort_values(pcol)
        for s, pr in zip(g["symbol"].iloc[-k:], g[pcol].iloc[-k:]):
            if pr > 0.5:
                out[(t, s)] = 1
        for s, pr in zip(g["symbol"].iloc[:k], g[pcol].iloc[:k]):
            if pr < 0.5:
                out[(t, s)] = -1
    return out


def evaluate(wf: pd.DataFrame, p: pd.DataFrame, seeds: int = 20) -> tuple[list[str], dict]:
    out, usable = [], {}
    pre = _prep(wf, p)
    days = np.sort(wf.index.unique())
    for hz, pcol, ycol in (("day", "p1", "y1"), ("week", "p5", "y5")):
        w = wf if hz == "day" else wf[wf.index.isin(days[::5])]          # weekly calls every 5th session: no overlapping trades
        cl = calls(w, pcol)
        syms_by_day = {t: list(g["symbol"]) for t, g in w.groupby(level=0)}
        real = summary(simulate(pre, hz, lambda pr, t, s: cl.get((t, s), 0)))
        called = wf.set_index("symbol", append=True)
        called = called[called.index.isin(list(cl))].dropna(subset=[ycol])
        side = np.array([cl[i] for i in called.index])
        hit = float(((side == 1) == (called[ycol] > 0)).mean()) if len(called) else float("nan")
        allw = wf.dropna(subset=[ycol])
        base = float((allw[ycol] > 0).mean())
        brier = float(((allw[pcol] - (allw[ycol] > 0)) ** 2).mean())
        brier0 = float(((base - (allw[ycol] > 0)) ** 2).mean())
        cover = float((allw[ycol].abs() <= 1.0).mean())
        ctrl = []
        for k in range(seeds):
            rng = np.random.default_rng(k)
            ctrl.append(summary(simulate(pre, hz, lambda pr, t, s, rng=rng: int(rng.choice([1, -1])) if (t, s) in cl else 0))["net"])
        lng = summary(simulate(pre, hz, lambda pr, t, s: 1 if (t, s) in cl else 0))
        picks = []                                                       # control 2: random NAMES each day, same number of longs / shorts
        for k in range(seeds):
            rng, rc = np.random.default_rng(100 + k), {}
            for t, names in syms_by_day.items():
                nl = sum(1 for (tt, _), v in cl.items() if tt == t and v == 1)
                ns = sum(1 for (tt, _), v in cl.items() if tt == t and v == -1)
                pick = list(rng.permutation(names))
                rc.update({(t, x): 1 for x in pick[:nl]})
                rc.update({(t, x): -1 for x in pick[nl:nl + ns]})
            picks.append(summary(simulate(pre, hz, lambda pr, t, s, rc=rc: rc.get((t, s), 0)))["net"])
        mid = days[len(days) // 2]
        halves = [summary(simulate(pre, hz, lambda pr, t, s, f=f: cl.get((t, s), 0) if f(t) else 0)) for f in (lambda t: t < mid, lambda t: t >= mid)]
        beat = sum(real["net"] > x for x in ctrl)
        beat2 = sum(real["net"] > x for x in picks)
        ok = (real["trades"] >= 100 and real["net"] > 0 and real["pf"] >= 1.1 and beat >= round(0.9 * seeds) and beat2 >= round(0.9 * seeds)
              and real["net"] > lng["net"] and all(h["net"] > 0 and h["pf"] >= 1.1 for h in halves))
        usable[hz] = ok
        out.append(f"- {hz.upper()}: {len(called)} calls (top/bottom {TOP_K} a day), direction right {hit:.1%} (up base {base:.1%}); Brier {brier:.4f} vs "
                   f"{brier0:.4f} no-skill; 1-sigma band held {cover:.0%} (68% expected) | levels {fmt(real)} | random side median {np.median(ctrl):+.1f}R, "
                   f"beaten {beat}/{seeds} | random names median {np.median(picks):+.1f}R, beaten {beat2}/{seeds} | same names always long {fmt(lng)} | halves {fmt(halves[0])} / {fmt(halves[1])} | {'USABLE' if ok else 'NOT usable - ranges only'}")
    return out, usable


def plan_today(p: pd.DataFrame, usable: dict) -> pd.DataFrame:
    last = p.index.max()
    days = np.sort(p.index.unique())
    f1, f5 = _fit(p[p.index <= days[-2]], "y1"), _fit(p[p.index <= days[-6]], "y5")
    z = p[p.index == last].dropna(subset=FEATS).copy()
    z["s1"], z["p1"] = f1(z)
    z["s5"], z["p5"] = f5(z)
    c1, c5 = calls(z, "p1"), calls(z, "p5")
    rows = []
    for t, r in z.iterrows():
        c, sg1 = r["close"], r["vol"] * r["close"]
        sg5 = sg1 * np.sqrt(5)
        row = {"symbol": r["symbol"], "close": c, "p_up_today": r["p1"], "today_lo": c - sg1, "today_hi": c + sg1, "p_up_7d": r["p5"],
               "week_lo": c - sg5, "week_hi": c + sg5}
        for hz, cl, sg, (pull, sk, tk) in (("day", c1, sg1, DAY_LVL), ("week", c5, sg5, WEEK_LVL)):
            side = cl.get((t, r["symbol"]), 0)
            if side and usable.get(hz):
                e = c - side * pull * sg
                row.update({f"{hz}_call": "BUY" if side == 1 else "SELL", f"{hz}_entry": e, f"{hz}_stop": e - side * sk * sg, f"{hz}_tp": e + side * tk * sg})
            else:
                row.update({f"{hz}_call": ("top pick" if side == 1 else "bottom pick" if side == -1 else "-") + ("" if usable.get(hz) or not side else " (unproven)"),
                            f"{hz}_entry": np.nan, f"{hz}_stop": np.nan, f"{hz}_tp": np.nan})
        rows.append(row)
    return pd.DataFrame(rows).set_index("symbol")


def report(daily_raw: dict, sector_of: dict, today: date, state_dir=None) -> list[str]:
    daily = {s: _dates(d) for s, d in daily_raw.items() if len(d)}
    daily = {s: d[d.index < pd.Timestamp(today)] for s, d in daily.items()}           # completed sessions only
    b = behaviour(daily, sector_of)
    out = [f"## forecast: behaviour (last 252 sessions to {max(d.index.max() for d in daily.values()).date()}), {len(b)} symbols"]
    etf = b[b.index.isin([s for s in b.index if s.startswith("XL") or s in ("SPY", "QQQ")])].sort_values("ret_1y", ascending=False)
    out.append("SECTORS / INDEX: symbol 1y | 3m | vol | maxDD | above200 | beta | day range | autocorr (<0 snaps back, >0 follows through)")
    for s, r in etf.iterrows():
        out.append(f"  {s:5s} {r.ret_1y:+6.1%} | {r.ret_3m:+6.1%} | {r.vol:5.1%} | {r.max_dd:6.1%} | {r.above200:4.0%} | {r.beta:4.2f} | {r.day_range:4.1%} | {r.ac:+.2f}")
    out.append("STOCKS by sector: symbol 1y | 3m | vs sector 1y | vol | maxDD | above200 | beta | day range | autocorr")
    st = b[~b.index.isin(etf.index)].sort_values(["sector", "ret_1y"], ascending=[True, False])
    for s, r in st.iterrows():
        out.append(f"  {r.sector:4s} {s:5s} {r.ret_1y:+6.1%} | {r.ret_3m:+6.1%} | {r.vs_sector:+6.1%} | {r.vol:5.1%} | {r.max_dd:6.1%} | {r.above200:4.0%} | "
                   f"{r.beta:4.2f} | {r.day_range:4.1%} | {r.ac:+.2f}")
    out.append(f"character: {int((b.ac < -0.05).sum())} snap-back names, {int((b.ac > 0.05).sum())} follow-through names, rest neutral; "
               f"median 1y {b.ret_1y.median():+.1%}, median maxDD {b.max_dd.median():.1%}")
    p = panel(daily, sector_of)
    wf = walk_forward(p)
    ev, usable = evaluate(wf, p)
    out.append("## forecast: walk-forward test, last 252 sessions (refit every 21, 5-day embargo, 10 bps a side)")
    out += ev
    pl = plan_today(p, usable)
    out.append(f"## forecast: plan for the next session and the next 7 days (from the {p.index.max().date()} close); "
               "BUY/SELL only where the horizon tested USABLE, else a lean + the 68% range")
    for s, r in pl.sort_values("p_up_today", ascending=False).iterrows():
        d = f"{r.day_call}" + (f" @{r.day_entry:.2f} stop {r.day_stop:.2f} tp {r.day_tp:.2f}" if np.isfinite(r.day_entry) else "")
        w = f"{r.week_call}" + (f" @{r.week_entry:.2f} stop {r.week_stop:.2f} tp {r.week_tp:.2f}" if np.isfinite(r.week_entry) else "")
        out.append(f"  {s:5s} {r.close:9.2f} | today P(up) {r.p_up_today:.0%} range {r.today_lo:.2f}-{r.today_hi:.2f} {d} | "
                   f"7d P(up) {r.p_up_7d:.0%} range {r.week_lo:.2f}-{r.week_hi:.2f} {w}")
    if state_dir is not None:
        from pathlib import Path
        sd = Path(state_dir)
        sd.mkdir(parents=True, exist_ok=True)
        pl.to_json(sd / f"plan-{today:%Y%m%d}.json", orient="index", indent=1)
        b.to_json(sd / f"behaviour-{today:%Y%m%d}.json", orient="index", indent=1)
    return out
