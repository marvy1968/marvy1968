"""Walk-forward backtest of the institutional hybrid simulation (marv/institutional.py) on CFB, PAPER research.

Rows: state/reports/pick_engine_cfb_games.csv (walk-forward pick-engine blend bm / bt per game, graded spread / total).
Openers: state/cache/cfb_lines_oc.csv (tools/cfb_open_lines.py, CFBD; Bovada first). BCR: state/cache/cfb_bcr.csv.
For each test season S: B / S / ST least squares on residuals of seasons < S (2021+ for steam: no openers in 2020),
skewnorm fits on the same earlier residuals; then 4,000 skew-MC draws per test game. Graded at the row's closing
line, -110. Compared on the SAME games with the plain pick engine (normal MC), a steam-only control and the market.
Usage: nice .venv/bin/python tools/institutional_bt.py [STATE_DIR] [first_test_season]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import skewnorm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marv import institutional as IN  # noqa: E402

BE = 0.5238


def rate(ok):
    ok = np.asarray(ok, bool)
    n, w = len(ok), int(np.asarray(ok, bool).sum())
    return {"n": n, "hit": round(w / n, 4) if n else None, "roi": round((w * 100 / 110 - (n - w)) / n, 4) if n else None}


def load(state):
    pe = pd.read_csv(state / "reports" / "pick_engine_cfb_games.csv", dtype={"game_id": str})
    oc = pd.read_csv(state / "cache" / "cfb_lines_oc.csv", dtype={"game_id": str})
    oc = oc[["season", "game_id", "spread_open", "spread", "total_open", "total"]].rename(
        columns={"spread": "bov_spread", "total": "bov_total"})
    df = pe.merge(oc, on=["season", "game_id"], how="left")
    # steam measured on ONE book (open and close from the same provider), applied to the graded line
    df["s_open"] = df.spread_open
    df["s_now"] = df.bov_spread
    df["t_open"] = df.total_open
    df["t_now"] = df.bov_total
    b = pd.read_csv(state / "cache" / "cfb_bcr.csv")
    bm = {(int(r.season), r.team): r.bcr for r in b.itertuples()}
    df["bcr_h"] = [bm.get((int(s), h), np.nan) for s, h in zip(df.season, df.home)]
    df["bcr_a"] = [bm.get((int(s), a), np.nan) for s, a in zip(df.season, df.away)]
    df["th"] = [IN.bcr_tier(x) for x in df.bcr_h]
    df["ta"] = [IN.bcr_tier(x) for x in df.bcr_a]
    df["tdiff"] = (df.th - df.ta).astype(float).fillna(0.0)
    df["es"] = [IN.steam(o, c) for o, c in zip(df.s_open, df.s_now)]
    df["et"] = [IN.steam(o, c) for o, c in zip(df.t_open, df.t_now)]
    df["date"] = pd.to_datetime(df.date)
    return df


def fit(tr):
    """B, S on margin residual (margin - bm); ST on total residual. Then skewnorm fits on the adjusted residuals."""
    X = np.column_stack([tr.tdiff, -tr.es])
    y = tr.margin - tr.bm
    B, S = np.linalg.lstsq(X, y, rcond=None)[0]
    st = tr[tr.total.notna()]
    ST = float(np.linalg.lstsq(st[["et"]].to_numpy(), (st.tot - st.bt).to_numpy(), rcond=None)[0][0])
    p = {"B": round(float(B), 3), "S": round(float(S), 3), "ST": round(ST, 3)}
    m = tr.bm + p["B"] * tr.tdiff - p["S"] * tr.es
    sg = np.where(m >= 0, 1.0, -1.0)
    rm = ((tr.margin - m) * sg).to_numpy()
    rt = (st.tot - (st.bt + p["ST"] * st.et)).to_numpy()
    p["skew_m"] = [round(float(v), 4) for v in skewnorm.fit(rm)]
    p["skew_t"] = [round(float(v), 4) for v in skewnorm.fit(rt)]
    return p


def acc(ok):
    ok = np.asarray(ok, bool)
    return {"n": len(ok), "hit": round(float(ok.mean()), 4) if len(ok) else None}


def grade(g):
    out = {}
    ar = g.margin + g.spread
    sp = g.spread.notna() & (ar != 0)
    orr = g.tot - g.total
    to = g.total.notna() & (orr != 0)
    has = sp & (g.margin != 0)
    y = (g.margin > 0).astype(float)
    out["market_fav_ml"] = acc(((g.spread < 0) == (g.margin > 0))[has])
    out["engine_ml"] = acc(((g.bm > 0) == (g.margin > 0))[has])
    out["inst_ml"] = acc(((g.im > 0) == (g.margin > 0))[has])
    out["engine_ats"] = rate(((g.pc > 0.5) == (ar > 0))[sp])
    out["inst_ats"] = rate(((g.ipc > 0.5) == (ar > 0))[sp])
    out["engine_ou"] = rate(((g.po > 0.5) == (orr > 0))[to])
    out["inst_ou"] = rate(((g.ipo > 0.5) == (orr > 0))[to])
    for lo in (0.05, 0.10):
        k = sp & ((g.ipc - 0.5).abs() >= lo)
        out[f"inst_ats_lean{int(lo * 100)}"] = rate(((g.ipc > 0.5) == (ar > 0))[k])
        k = to & ((g.ipo - 0.5).abs() >= lo)
        out[f"inst_ou_lean{int(lo * 100)}"] = rate(((g.ipo > 0.5) == (orr > 0))[k])
    st = sp & (g.es.abs() >= 0.5)
    out["steam_only_ats"] = rate(((g.es < 0) == (ar > 0))[st])  # follow the move on the closing number
    k = sp & (g.es.abs() >= 0.5)
    out["inst_ats_when_steam"] = rate(((g.ipc > 0.5) == (ar > 0))[k])
    k = sp & g.guard.notna()
    out["bcr_guard_fav_ats"] = rate(((g.guard == "home") == (ar > 0))[k])
    out["brier_engine"] = round(float(((g.ph - y) ** 2)[has].mean()), 4)
    out["brier_inst"] = round(float(((g.iph - y) ** 2)[has].mean()), 4)
    out["margin_mae_engine"] = round(float((g.bm - g.margin).abs()[sp].mean()), 2)
    out["margin_mae_inst"] = round(float((g.im - g.margin).abs()[sp].mean()), 2)
    out["margin_mae_line"] = round(float((-g.spread - g.margin).abs()[sp].mean()), 2)
    out["with_opener"] = int(g.s_open.notna().sum())
    out["with_bcr_both"] = int((g.th.notna() & g.ta.notna()).sum())
    return out


def run(state, first_test=2025):
    df = load(state)
    df = df[df.season >= 2021].dropna(subset=["bm", "bt", "margin", "tot"]).reset_index(drop=True)  # openers start 2021
    rows, fits = [], {}
    for s in sorted(df.season.unique()):
        if s < first_test:
            continue
        tr, te = df[df.season < s], df[df.season == s].copy()
        p = fit(tr)
        fits[int(s)] = p
        sims = [IN.institutional_hybrid_simulation(r.bm, r.bt, r.bcr_h, r.bcr_a, r.spread, r.total,
                                                   None, None, p, n=4000, seed=i) for i, r in enumerate(te.itertuples())]
        # steam: measured open->close on the steam book, applied on top (graded line may be another book)
        te["im"] = te.bm + p["B"] * te.tdiff - p["S"] * te.es
        te["it"] = te.bt + p["ST"] * te.et
        res = [IN.simulate(m, t, p, sp_, ln, n=4000, seed=i)
               for i, (m, t, sp_, ln) in enumerate(zip(te.im, te.it, te.spread, te.total))]
        te["iph"] = [x["p_home"] for x in res]
        te["ipc"] = [x.get("p_home_cover", np.nan) for x in res]
        te["ipo"] = [x.get("p_over", np.nan) for x in res]
        te["guard"] = [x["bcr_guard"] for x in sims]
        rows.append(te)
        print("fit", s, p, flush=True)
    g = pd.concat(rows, ignore_index=True)
    last = g.date.max()
    w3 = g[g.date > last - pd.Timedelta(days=21)]
    rep = {"note": "walk-forward; every coefficient fit on earlier seasons only; graded at the closing line @-110; PAPER",
           "by_season": {int(s): grade(d) for s, d in g.groupby("season")},
           "window_3wk": {"from": str((last - pd.Timedelta(days=21)).date()), "to": str(last.date()), **grade(w3)},
           "all_tests": grade(g), "fits": fits, "latest_fit": fit(df)}
    return rep, g


if __name__ == "__main__":
    state = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/marv-bot/state")
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 2025
    rep, g = run(state, first)
    (state / IN.REPORT).write_text(json.dumps(rep, indent=1, default=str))
    g.to_csv(state / "reports" / "institutional_cfb_games.csv", index=False)
    print(json.dumps({k: rep[k] for k in ("by_season", "window_3wk", "all_tests", "latest_fit")}, indent=1, default=str))
