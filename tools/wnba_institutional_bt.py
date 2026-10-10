"""Walk-forward backtest of the WNBA institutional simulation (marv/wnba_institutional.py), PAPER research.

Rows: state/reports/pick_engine_wnba_games.csv (walk-forward pick-engine blend bm / bt per game, graded at its ESPN
closing spread / total). Steam: state/cache/wnba_open_lines.csv (tools/wnba_open_lines.py, ESPN pregame books' own open
-> close, median). Net rating / TS / def TS: EWM of earlier games this season. Rotation depth: player box minutes
(2022+ only). For each test season S: margin / total coefficients least squares on blend residuals of seasons < S
(2022+ rows, the seasons with player minutes); margin skew shape fitted on the same residuals then rescaled to std 7.5
(owner setting); total skewnorm fitted freely. 4,000 draws per game. Compared on the SAME games with the plain pick
engine (normal MC, fitted sd), single-feature follow rules and the market.
Usage: nice .venv/bin/python tools/wnba_institutional_bt.py [STATE_DIR] [first_test_season]
"""
import json
import sys
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import skewnorm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marv import wnba_institutional as WI  # noqa: E402

MX = ["net_d", "ts_d", "depth_d", "miss_d", "es"]
MK = ["N", "T", "D", "M", "S"]
TX = ["ts_s", "miss_s", "et"]
TK = ["TT", "MT", "ST"]
FIRST_ROT = 2022
MIN_OPEN = 150


def rate(ok):
    ok = np.asarray(ok, bool)
    n, w = len(ok), int(ok.sum())
    return {"n": n, "hit": round(w / n, 4) if n else None, "roi": round((w * 100 / 110 - (n - w)) / n, 4) if n else None}


def acc(ok):
    ok = np.asarray(ok, bool)
    return {"n": len(ok), "hit": round(float(ok.mean()), 4) if len(ok) else None}


def load(state: Path) -> pd.DataFrame:
    df = pd.read_csv(state / "reports" / "pick_engine_wnba_games.csv", dtype={"game_id": str})
    df["date"] = pd.to_datetime(df.date)
    f = WI.game_features(state / "cache", range(int(df.season.min()), int(df.season.max()) + 1))
    f["game_id"] = f.game_id.astype(str)
    df = df.merge(f, on="game_id", how="left")
    ol = state / "cache" / "wnba_open_lines.csv"
    if ol.exists():
        o = pd.read_csv(ol, dtype={"game_id": str})[["game_id", "s_open", "s_close", "t_open", "t_close"]]
        df = df.merge(o, on="game_id", how="left")
    else:
        df["s_open"] = df["s_close"] = df["t_open"] = df["t_close"] = np.nan
    # steam = book open -> book close (same books); applied on top of the graded closing line
    df["es"] = [WI.steam(a, b) for a, b in zip(df.s_open, df.s_close)]
    df["et"] = [WI.steam(a, b) for a, b in zip(df.t_open, df.t_close)]
    for c in ["net_d", "ts_d", "ts_s", "depth_d", "miss_d", "miss_s"]:
        df[c + "_raw"] = df[c]
        df[c] = df[c].fillna(0.0)
    df["has_rot"] = df.has_rot.fillna(False).astype(bool)
    return df


def fit(tr: pd.DataFrame) -> dict:
    tr = tr[tr.has_rot] if tr.has_rot.any() else tr
    X = tr[MX].to_numpy(float).copy()
    X[:, -1] *= -1  # margin -= S * steam
    steam_ok = int(tr.s_open.notna().sum()) >= MIN_OPEN  # too few openers (2023 has 2) -> steam terms stay 0
    y = (tr.margin - tr.bm).to_numpy(float)
    lam = 1e-3 * len(tr)
    if not steam_ok:
        X[:, -1] = 0.0
    beta = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ y)
    st = tr[tr.tot.notna()]
    XT = st[TX].to_numpy(float).copy()
    if not steam_ok:
        XT[:, -1] = 0.0
    yt = (st.tot - st.bt).to_numpy(float)
    bt = np.linalg.solve(XT.T @ XT + lam * np.eye(XT.shape[1]), XT.T @ yt)
    p = {k: round(float(v), 4) for k, v in zip(MK + TK, list(beta) + list(bt))}
    m = adj_m(tr, p)
    rm = ((tr.margin - m) * np.where(m >= 0, 1.0, -1.0)).to_numpy()
    rt = (st.tot - adj_t(st, p)).to_numpy()
    p["skew_a"] = round(float(skewnorm.fit(rm)[0]), 4)
    p["sd_m"] = WI.SD_MARGIN
    p["fitted_sd_m"] = round(float(rm.std()), 3)
    p["skew_t"] = [round(float(v), 4) for v in skewnorm.fit(rt)]
    p["n_train"] = int(len(tr))
    p["train_seasons"] = sorted(int(s) for s in tr.season.unique())
    return p


def adj_m(d, p):
    return d.bm + p["N"] * d.net_d + p["T"] * d.ts_d + p["D"] * d.depth_d + p["M"] * d.miss_d - p["S"] * d.es


def adj_t(d, p):
    return d.bt + p["TT"] * d.ts_s + p["MT"] * d.miss_s + p["ST"] * d.et


def grade(g: pd.DataFrame) -> dict:
    out = {}
    ar = g.margin + g.spread
    sp = g.spread.notna() & (ar != 0)
    orr = g.tot - g.total
    to = g.total.notna() & (orr != 0)
    has = g.margin != 0
    y = (g.margin > 0).astype(float)
    out["market_fav_ml"] = acc(((g.spread < 0) == (g.margin > 0))[sp & (g.spread != 0)])
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
    k = sp & (g.es.abs() >= 0.5)
    out["steam_follow_ats"] = rate(((g.es < 0) == (ar > 0))[k])
    k = to & (g.et.abs() >= 0.5)
    out["steam_follow_ou"] = rate(((g.et > 0) == (orr > 0))[k])
    k = sp & (g.miss_d_raw.abs() >= 0.5)
    out["healthier_rotation_ats"] = rate(((g.miss_d > 0) == (ar > 0))[k])
    k = sp & (g.depth_d_raw.abs() >= 2)
    out["deeper_rotation_ats"] = rate(((g.depth_d > 0) == (ar > 0))[k])
    out["brier_engine"] = round(float(((g.ph - y) ** 2)[has].mean()), 4)
    out["brier_inst_sd7.5"] = round(float(((g.iph - y) ** 2)[has].mean()), 4)
    out["brier_inst_fitted_sd"] = round(float(((g.iph_fit - y) ** 2)[has].mean()), 4)
    sd = 11.5
    mk = np.array([0.5 * (1 + erf(-s / (sd * sqrt(2)))) for s in g.spread.fillna(0)])
    out["brier_market_spread"] = round(float(((mk - y) ** 2)[has & g.spread.notna()].mean()), 4)
    out["margin_mae_engine"] = round(float((g.bm - g.margin).abs()[sp].mean()), 2)
    out["margin_mae_inst"] = round(float((g.im - g.margin).abs()[sp].mean()), 2)
    out["margin_mae_line"] = round(float((-g.spread - g.margin).abs()[sp].mean()), 2)
    out["total_mae_engine"] = round(float((g.bt - g.tot).abs()[to].mean()), 2)
    out["total_mae_inst"] = round(float((g.it - g.tot).abs()[to].mean()), 2)
    out["total_mae_line"] = round(float((g.total - g.tot).abs()[to].mean()), 2)
    out["with_opener"] = int(g.s_open.notna().sum())
    out["with_rotation"] = int(g.has_rot.sum())
    out["games"] = int(len(g))
    return out


def run(state: Path, first_test: int = 2023):
    df = load(state).dropna(subset=["bm", "bt", "margin", "tot"]).reset_index(drop=True)
    rows, fits = [], {}
    for s in sorted(df.season.unique()):
        if s < first_test:
            continue
        tr, te = df[(df.season < s) & (df.season >= FIRST_ROT)], df[df.season == s].copy()
        p = fit(tr)
        fits[int(s)] = p
        te["im"], te["it"] = adj_m(te, p), adj_t(te, p)
        res = [WI.simulate(m, t, p, sp_, ln, n=4000, seed=i)
               for i, (m, t, sp_, ln) in enumerate(zip(te.im, te.it, te.spread, te.total))]
        pf = {**p, "sd_m": p["fitted_sd_m"]}
        resf = [WI.simulate(m, t, pf, None, None, n=4000, seed=i) for i, (m, t) in enumerate(zip(te.im, te.it))]
        te["iph"] = [x["p_home"] for x in res]
        te["iph_fit"] = [x["p_home"] for x in resf]
        te["ipc"] = [x.get("p_home_cover", np.nan) for x in res]
        te["ipo"] = [x.get("p_over", np.nan) for x in res]
        rows.append(te)
        print("fit", s, p, flush=True)
    g = pd.concat(rows, ignore_index=True)
    last = g.date.max()
    w3 = g[g.date > last - pd.Timedelta(days=21)]
    rep = {"note": "walk-forward; every coefficient fit on earlier seasons only (2022+ rows); graded at the ESPN closing "
                   "line @-110; margin std fixed at 7.5 (owner setting); PAPER",
           "by_season": {int(s): grade(d) for s, d in g.groupby("season")},
           "window_3wk": {"from": str((last - pd.Timedelta(days=21)).date()), "to": str(last.date()), **grade(w3)},
           "all_tests": grade(g), "fits": fits,
           "latest_fit": fit(df[df.season >= FIRST_ROT])}
    return rep, g


if __name__ == "__main__":
    state = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/marv-bot/state")
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 2023
    rep, g = run(state, first)
    (state / WI.REPORT).write_text(json.dumps(rep, indent=1, default=str))
    g.to_csv(state / "reports" / "wnba_institutional_games.csv", index=False)
    print(json.dumps({k: rep[k] for k in ("by_season", "window_3wk", "all_tests", "latest_fit")}, indent=1, default=str))
