"""Weekly picks for OANDA (owner 2026-10-29: "can we add it to oanda trend experts"): the stock weekly-picks idea ported to the 7 majors.

PRE-DECLARED before any run. Daily mid bars of the 7 USD majors. The pooled model of alpacabot.forecast (same features; the "market" is the
equal-weight basket of the 7 pairs, no sector) predicts each pair's next-5-day return RELATIVE to the basket, in vol units. Every 5th
session: LONG the top-ranked pair and SHORT the bottom-ranked pair at the next open; stop = the 10-day low (short: high), moved to entry +
cost once price covers half the way to the target; target = entry + 1.5 x 5-day sigma; out at the 5th close. Costs 1.5 bps a side (3 bps stress).
  PURE   = the model's picks.
  HYBRID = only picks our trend experts agree with: a long needs the pair's long trend UP (fxbot.strongtrend long_state 1) and no daily
           reversal against it; a short needs long_state -1. If the top / bottom pair fails, the next one in rank that passes is used.
TEST: walk-forward over the last 252 sessions, refit every 21 (5-day embargo). Control: random pair and side on the same days with the same
exits (20 seeds). USABLE: 30+ trades, net R > 0, PF >= 1.1, net > 0 in each half, beats 18/20 random runs, still net > 0 at 3 bps.
"""

from datetime import date

import numpy as np
import pandas as pd

from alpacabot import forecast as v1

COST1, COST2 = 1.5 / 1e4, 3.0 / 1e4


def mid(d: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({k: (d["b" + k[0]] + d["a" + k[0]]) / 2 for k in ("open", "high", "low", "close")}, index=d.index)
    out.index = pd.to_datetime([pd.Timestamp(x).tz_convert("America/New_York").date() if pd.Timestamp(x).tzinfo else pd.Timestamp(x).date()
                                for x in out.index])
    return out[~out.index.duplicated(keep="last")].sort_index()


def panel(daily: dict) -> pd.DataFrame:
    idx = sorted(set().union(*[d.index for d in daily.values()]))
    rets = pd.DataFrame({s: np.log(d["close"]).diff() for s, d in daily.items()}).reindex(idx)
    basket = pd.DataFrame({"close": np.exp(rets.mean(axis=1).fillna(0).cumsum()) * 100})
    for k in ("open", "high", "low"):
        basket[k] = basket["close"]
    out = []
    for s, d in daily.items():
        f = v1.features(d, basket, None)
        c = d["close"]
        bc = basket["close"].reindex(d.index).ffill()
        f["y5rel"] = (np.log(c.shift(-5) / c) - np.log(bc.shift(-5) / bc)) / (f["vol"] * np.sqrt(5))
        f["symbol"] = s
        f["o"], f["h"], f["l"] = d["open"], d["high"], d["low"]
        out.append(f)
    return pd.concat(out).sort_index()


def _fit(tr: pd.DataFrame):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    tr = tr.dropna(subset=v1.FEATS + ["y5rel"])
    y = tr["y5rel"].clip(-4, 4)
    r = Ridge(alpha=10.0).fit(tr[v1.FEATS], y)
    g = HistGradientBoostingRegressor(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=100, l2_regularization=1.0,
                                      random_state=0).fit(tr[v1.FEATS], y)
    return lambda z: 0.5 * r.predict(z[v1.FEATS]) + 0.5 * g.predict(z[v1.FEATS])


def trade(arr, s, t, side, sig5, cost) -> float | None:
    pos, o, h, l, c = arr[s]
    i = pos.get(t)
    if i is None or i + 5 >= len(o) or i < 10:
        return None
    entry = o[i + 1] * (1 + side * cost)
    stop = l[i - 9:i + 1].min() if side == 1 else h[i - 9:i + 1].max()
    if side * (entry - stop) <= 0:
        return None
    risk, tgt = side * (entry - stop), entry + side * 1.5 * sig5
    be = False
    for k in range(i + 1, i + 6):
        if (side == 1 and l[k] <= stop) or (side == -1 and h[k] >= stop):
            px = o[k] if k > i + 1 and side * (o[k] - stop) <= 0 else stop
            return side * (px * (1 - side * cost) - entry) / risk
        if (side == 1 and h[k] >= tgt) or (side == -1 and l[k] <= tgt):
            px = o[k] if k > i + 1 and side * (o[k] - tgt) >= 0 else tgt
            return side * (px * (1 - side * cost) - entry) / risk
        if not be and side * ((h[k] if side == 1 else l[k]) - entry) >= 0.5 * abs(tgt - entry):
            stop, be = entry * (1 + side * cost), True
    return side * (c[i + 5] * (1 - side * cost) - entry) / risk


def picks(g: pd.DataFrame, trend: dict, t, hybrid: bool) -> list[tuple[str, int]]:
    g = g.sort_values("rel")
    out = []
    for side, order in ((1, g.iloc[::-1]), (-1, g)):
        for r in order.itertuples():
            if hybrid:
                ls, rv = trend.get(r.symbol, {}).get(t, (0, 0))
                if ls != side or rv == -side:
                    continue
            elif (side == 1 and r.rel <= 0) or (side == -1 and r.rel >= 0):
                break
            if all(r.symbol != s for s, _ in out):
                out.append((r.symbol, side))
            break
    return out


def report(raw: dict, today: date, seeds: int = 20) -> list[str]:
    from fxbot.strongtrend import reversal, votes
    daily = {s: mid(d) for s, d in raw.items() if len(d) > 400}
    daily = {s: d[d.index < pd.Timestamp(today)] for s, d in daily.items()}
    p = panel(daily)
    trend = {}
    for s, d in daily.items():
        ls, rv = votes(d)["long_state"].to_numpy(int), reversal(d)
        trend[s] = {t: (int(a), int(b)) for t, a, b in zip(d.index, ls, rv)}
    days = pd.DatetimeIndex(np.sort(p.index.unique()))
    test = days[-253:-1]
    wf = []
    for k in range(0, len(test), 21):
        block = test[k:k + 21]
        hist = p[p.index < days[days.get_loc(block[0]) - 5]]
        f = _fit(hist)
        z = p[p.index.isin(block)].dropna(subset=v1.FEATS).copy()
        z["rel"] = f(z)
        wf.append(z)
    wf = pd.concat(wf)
    arr = {s: ({t: i for i, t in enumerate(g.index)}, *(g[k].to_numpy(float) for k in ("o", "h", "l", "close"))) for s, g in p.groupby("symbol")}
    reb = pd.DatetimeIndex(np.sort(wf.index.unique()))[::5]
    mid_t = reb[len(reb) // 2]
    out = [f"## weekpicks_fx: 7 majors, weekly long top / short bottom by predicted return vs the basket; walk-forward {reb[0].date()}..{reb[-1].date()}"]
    for name, hybrid in (("PURE", False), ("HYBRID (trend experts agree)", True)):
        r1, r2, halves, n_long, n_short = [], [], ([], []), 0, 0
        for t in reb:
            g = wf.loc[[t]]
            for s, side in picks(g, trend, t, hybrid):
                row = g[g["symbol"] == s].iloc[0]
                sig5 = row["vol"] * row["close"] * np.sqrt(5)
                x, y = trade(arr, s, t, side, sig5, COST1), trade(arr, s, t, side, sig5, COST2)
                if x is not None:
                    r1.append(x)
                    r2.append(y if y is not None else x)
                    (halves[0] if t < mid_t else halves[1]).append(x)
                    n_long += side == 1
                    n_short += side == -1
        ctrl = []
        for k in range(seeds):
            rng, rs = np.random.default_rng(k), []
            for t in reb:
                g = wf.loc[[t]]
                n = len(picks(g, trend, t, hybrid))
                for s in rng.choice(list(g["symbol"]), size=min(n, len(g)), replace=False):
                    row = g[g["symbol"] == s].iloc[0]
                    x = trade(arr, s, t, int(rng.choice([1, -1])), row["vol"] * row["close"] * np.sqrt(5), COST1)
                    if x is not None:
                        rs.append(x)
            ctrl.append(v1.summary(rs)["net"])
        s1, s2 = v1.summary(r1), v1.summary(r2)
        h0, h1 = v1.summary(halves[0]), v1.summary(halves[1])
        beat = sum(s1["net"] > x for x in ctrl)
        ok = s1["trades"] >= 30 and s1["net"] > 0 and s1["pf"] >= 1.1 and h0["net"] > 0 and h1["net"] > 0 and beat >= round(0.9 * seeds) and s2["net"] > 0
        out.append(f"- {name}: {v1.fmt(s1)} ({n_long} long, {n_short} short) | 3 bps {v1.fmt(s2)} | halves {v1.fmt(h0)} / {v1.fmt(h1)} | random "
                   f"median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds} | {'USABLE' if ok else 'NOT usable'}")
    last = days[-1]
    f = _fit(p[p.index <= days[-6]])
    z = p[p.index == last].dropna(subset=v1.FEATS).copy()
    z["rel"] = f(z)
    out.append(f"now ({last.date()} close): " + ", ".join(f"{r.symbol} {r.rel:+.2f} trend {trend[r.symbol].get(last, (0, 0))[0]:+d}"
                                                    for r in z.sort_values("rel", ascending=False).itertuples())
               + f" | pure picks {picks(z, trend, last, False)} | hybrid picks {picks(z, trend, last, True)}")
    return out
