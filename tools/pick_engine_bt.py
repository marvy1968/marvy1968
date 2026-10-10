"""Walk-forward backtest: pick engine (power rating + trend analyzer + Monte Carlo) vs the old system (refined hybrid,
marv/hybrid.py, walk-forward rows from tools/hybrid_refine_bt.py) on CFB / NFL / WNBA. PAPER research.

Power ratings refit before every game date on earlier games only; blend weights / residual sd fit on earlier seasons
only; graded at closing / consensus lines, -110. Windows: last 21 days with lines ("3-week") and the full walk-forward.
Usage: nice .venv/bin/python tools/pick_engine_bt.py [cfb|nfl|wnba|all] [STATE_DIR]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marv import pick_engine as PE  # noqa: E402

BE = 0.5238
SEASONS = {"cfb": range(2018, 2027), "nfl": range(2020, 2027), "wnba": range(2018, 2027)}


def rate(ok):
    ok = np.asarray(ok, bool)
    n = len(ok); w = int(ok.sum())
    return {"n": n, "hit": round(w / n, 4) if n else None, "roi": round((w * 100 / 110 - (n - w)) / n, 4) if n else None}


def acc(ok):
    ok = np.asarray(ok, bool)
    return {"n": len(ok), "hit": round(float(ok.mean()), 4) if len(ok) else None}


def walk_power(res, sport):
    pm, pt = np.full(len(res), np.nan), np.full(len(res), np.nan)
    for d, idx in res.groupby(res.date.dt.normalize()).groups.items():
        hist = res[(res.date < d) & (res.season >= res.loc[idx[0], "season"] - 1)]
        pw = PE.fit_power(hist, d, sport)
        if not pw:
            continue
        for i in idx:
            r = res.loc[i]
            x = PE.power_read(pw, r.home, r.away, bool(r.neutral))
            if x:
                pm[i], pt[i] = x
    return pm, pt


def fit_blend(tr):
    X = np.column_stack([(~tr.neutral.astype(bool)).astype(float), tr.pm, tr.proj])
    cs = np.linalg.lstsq(X, tr.margin, rcond=None)[0]
    Xt = np.column_stack([np.ones(len(tr)), tr.pt, tr.tot_hat])
    ct = np.linalg.lstsq(Xt, tr.tot, rcond=None)[0]
    rm, rt = tr.margin - X @ cs, tr.tot - Xt @ ct
    return {"side": [round(float(v), 4) for v in cs], "tot": [round(float(v), 4) for v in ct],
            "sd_m": round(float(rm.std()), 3), "sd_t": round(float(rt.std()), 3),
            "rho": round(float(np.corrcoef(rm, rt)[0, 1]), 3)}


def grade(df):
    out = {}
    dec = df.margin != 0
    has = df.spread.notna() & (df.spread != 0) & dec
    out["market_fav_ml"] = acc(((df.spread < 0) == (df.margin > 0))[has])
    for name, m in (("old_hybrid", df.proj), ("power_only", df.pm), ("engine", df.bm)):
        out[f"{name}_ml"] = acc(((m > 0) == (df.margin > 0))[has])
    ar = df.margin + df.spread
    sp = df.spread.notna() & (ar != 0)
    orr = df.tot - df.total
    to = df.total.notna() & (orr != 0)
    for name, m, t in (("old_hybrid", df.proj, df.tot_hat), ("power_only", df.pm, df.pt), ("engine", df.bm, df.bt)):
        e = m + df.spread
        out[f"{name}_ats"] = rate(((e > 0) == (ar > 0))[sp & (e != 0)])
        g = t - df.total
        out[f"{name}_ou"] = rate(((g > 0) == (orr > 0))[to & (g != 0)])
    # engine strength tiers (MC lean) and 3/3 agreement
    for lo in (0.05, 0.10):
        k = sp & ((df.pc - 0.5).abs() >= lo)
        out[f"engine_ats_lean{int(lo * 100)}"] = rate(((df.pc > 0.5) == (ar > 0))[k])
        k = to & ((df.po - 0.5).abs() >= lo)
        out[f"engine_ou_lean{int(lo * 100)}"] = rate(((df.po > 0.5) == (orr > 0))[k])
    a1, a2, a3 = (df.pm + df.spread) > 0, (df.proj + df.spread) > 0, df.pc > 0.5
    k = sp & (a1 == a2) & (a2 == a3)
    out["engine_ats_3of3"] = rate((a3 == (ar > 0))[k])
    out["engine_margin_mae"] = round(float((df.bm - df.margin).abs()[sp].mean()), 2)
    out["old_margin_mae"] = round(float((df.proj - df.margin).abs()[sp].mean()), 2)
    out["line_margin_mae"] = round(float((-df.spread - df.margin).abs()[sp].mean()), 2)
    # Brier of the engine's MC win prob vs a spread-implied normal prob (market proxy)
    from math import erf, sqrt
    sdm = df.sd_m.iloc[0] if len(df) else 13
    mk = np.array([0.5 * (1 + erf(-s / (sdm * sqrt(2)))) for s in df.spread.fillna(0)])
    y = (df.margin > 0).astype(float)
    out["engine_brier"] = round(float(((df.ph - y) ** 2)[has].mean()), 4)
    out["market_spread_brier"] = round(float(((mk - y) ** 2)[has].mean()), 4)
    return out


def per_season(df, col_ok, mask):
    return {int(s): rate(d[col_ok][d.index.isin(mask[mask].index)]) for s, d in df.groupby("season")}


def run(sport, state):
    cache = state / "cache"
    res = PE.results(cache, sport, list(SEASONS[sport]))
    hy = pd.read_csv(state / "reports" / f"hybrid_refine_{sport}_games.csv", dtype={"game_id": str})
    hy = hy[["game_id", "season", "margin", "tot", "spread", "total", "proj", "tot_hat"]]
    res = res.drop_duplicates("game_id").reset_index(drop=True)
    res["pm"], res["pt"] = walk_power(res, sport)
    df = hy.merge(res[["game_id", "date", "home", "away", "neutral", "pm", "pt"]], on="game_id")
    df = df.dropna(subset=["pm", "proj", "tot_hat", "pt"]).reset_index(drop=True)
    seasons = sorted(df.season.unique())
    rows, fits = [], {}
    for s in seasons[1:]:
        tr, te = df[df.season < s], df[df.season == s].copy()
        if len(tr) < 150 or te.empty:
            continue
        f = fit_blend(tr)
        fits[int(s)] = f
        te["bm"], te["bt"] = zip(*[PE.blend(sport, r.pm, r.proj, r.pt, r.tot_hat, bool(r.neutral), f) for r in te.itertuples()])
        sims = [PE.simulate(r.bm, r.bt, f["sd_m"], f["sd_t"], f["rho"], r.spread, r.total, n=4000, seed=i)
                for i, r in enumerate(te.itertuples())]
        te["ph"] = [x["p_home"] for x in sims]
        te["pc"] = [x.get("p_home_cover", np.nan) for x in sims]
        te["po"] = [x.get("p_over", np.nan) for x in sims]
        te["sd_m"] = f["sd_m"]
        rows.append(te)
        print(sport, s, f, flush=True)
    g = pd.concat(rows, ignore_index=True)
    latest = fit_blend(df)  # every completed game -> the live blend for the current season
    last = g[g.spread.notna()].date.max()
    w3 = g[g.date > last - pd.Timedelta(days=21)]
    rep = {"window_3wk": {"from": str((last - pd.Timedelta(days=21)).date()), "to": str(last.date()), **grade(w3)},
           "full": grade(g), "fits": fits, "latest_fit": latest}
    ar = g.margin + g.spread
    e_ok = (g.pc > 0.5) == (ar > 0)
    sp = g.spread.notna() & (ar != 0)
    rep["full"]["engine_ats_by_season"] = {int(s): rate(e_ok[sp & (g.season == s)]) for s in g.season.unique()}
    o_ok = (g.proj + g.spread > 0) == (ar > 0)
    rep["full"]["old_ats_by_season"] = {int(s): rate(o_ok[sp & (g.season == s)]) for s in g.season.unique()}
    orr = g.tot - g.total
    to = g.total.notna() & (orr != 0)
    eo = (g.po > 0.5) == (orr > 0)
    rep["full"]["engine_ou_by_season"] = {int(s): rate(eo[to & (g.season == s)]) for s in g.season.unique()}
    return rep, g


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    state = Path(sys.argv[2] if len(sys.argv) > 2 else "/opt/marv-bot/state")
    path = state / PE.REPORT
    try:
        out = json.loads(path.read_text())
    except (OSError, ValueError):
        out = {}
    for sp in (["cfb", "nfl", "wnba"] if which == "all" else [which]):
        r, g = run(sp, state)
        out[sp] = r
        g.to_csv(state / "reports" / f"pick_engine_{sp}_games.csv", index=False)
        path.write_text(json.dumps(out, indent=1, default=str))
        print(sp, json.dumps({"window_3wk": r["window_3wk"], "full": {k: v for k, v in r["full"].items()}}, default=str), flush=True)
