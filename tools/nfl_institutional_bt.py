"""Walk-forward backtest of the NFL institutional simulation (marv/nfl_institutional.py), PAPER research.

Rows: state/reports/pick_engine_nfl_games.csv (walk-forward pick-engine blend bm / bt per game, graded at its closing
spread / total). Steam: Bovada snapshots state/cache/bovada_nfl_hist/ (tools/fetch_bovada_nfl.py) -> first snapshot
that lists the game (opener, median ~6 days out) and the last one before kickoff, same book. O-line health: nflverse
snap counts + injury reports (marv/nfl_institutional.ol_lost, only data known before the game).
For each test season S: O / OT / S / ST least squares on blend residuals of seasons < S; margin skew shape fitted on
the same earlier residuals then rescaled to std 13.5; total skewnorm fitted freely. 4,000 draws per game. Compared on
the SAME games with the plain pick engine (normal MC), a steam-only follow rule, an O-line-only rule and the market.
Usage: nice .venv/bin/python tools/nfl_institutional_bt.py [STATE_DIR] [first_test_season]
"""
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import skewnorm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marv import nfl_institutional as NI  # noqa: E402
from marv.data.teams import NFL_TEAMS  # noqa: E402


def rate(ok):
    ok = np.asarray(ok, bool)
    n, w = len(ok), int(ok.sum())
    return {"n": n, "hit": round(w / n, 4) if n else None, "roi": round((w * 100 / 110 - (n - w)) / n, 4) if n else None}


def acc(ok):
    ok = np.asarray(ok, bool)
    return {"n": len(ok), "hit": round(float(ok.mean()), 4) if len(ok) else None}


def bovada_lines(cache: Path) -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(cache / "bovada_nfl_hist" / "*.json"))):
        snap = os.path.basename(f)[:-5]
        for ev in json.load(open(f)).get("data", []):
            if ev["commence_time"] <= snap:
                continue
            sp = tt = None
            for b in ev.get("bookmakers", []):
                for m in b["markets"]:
                    if m["key"] == "spreads":
                        sp = next((o["point"] for o in m["outcomes"] if o["name"] == ev["home_team"]), None)
                    elif m["key"] == "totals" and m["outcomes"]:
                        tt = m["outcomes"][0]["point"]
            rows.append((snap, ev["commence_time"], ev["home_team"], sp, tt))
    r = pd.DataFrame(rows, columns=["snap", "ct", "home", "sp", "tt"]).sort_values("snap")
    r["date"] = pd.to_datetime(r.ct).dt.tz_convert("America/New_York").dt.date.astype(str)
    out = []
    for (d, h), g in r.groupby(["date", "home"]):
        s, t = g[g.sp.notna()], g[g.tt.notna()]
        out.append({"date": d, "home": h, "s_open": s.sp.iloc[0] if len(s) else np.nan,
                    "s_now": s.sp.iloc[-1] if len(s) else np.nan, "t_open": t.tt.iloc[0] if len(t) else np.nan,
                    "t_now": t.tt.iloc[-1] if len(t) else np.nan})
    return pd.DataFrame(out)


def load(state: Path) -> pd.DataFrame:
    df = pd.read_csv(state / "reports" / "pick_engine_nfl_games.csv", dtype={"game_id": str})
    df["date"] = pd.to_datetime(df.date)
    bl = bovada_lines(state / "cache")
    bl["date"] = pd.to_datetime(bl.date)
    df = df.merge(bl, on=["date", "home"], how="left")
    p = df.game_id.str.split("_", expand=True)
    df["week"], df["a_ab"], df["h_ab"] = p[1].astype(int), p[2], p[3]
    lh, la = [], []
    for s, part in df.groupby("season"):
        sn, inj = NI.load_season(state / "cache", int(s))
        for r in part.itertuples():
            lh.append((r.Index, NI.ol_lost(sn, inj, NI.TEAM_FIX.get(r.h_ab, r.h_ab), r.week)))
            la.append((r.Index, NI.ol_lost(sn, inj, NI.TEAM_FIX.get(r.a_ab, r.a_ab), r.week)))
    df["lost_h"] = pd.Series(dict(lh))
    df["lost_a"] = pd.Series(dict(la))
    df["ol_d"] = (df.lost_a.fillna(0) - df.lost_h.fillna(0)).astype(float)
    df["ol_s"] = (df.lost_a.fillna(0) + df.lost_h.fillna(0)).astype(float)
    df["es"] = [NI.steam(o, c) for o, c in zip(df.s_open, df.s_now)]
    df["et"] = [NI.steam(o, c) for o, c in zip(df.t_open, df.t_now)]
    return df


def fit(tr: pd.DataFrame) -> dict:
    y = tr.margin - tr.bm
    O, S = np.linalg.lstsq(np.column_stack([tr.ol_d, -tr.es]), y, rcond=None)[0]
    if O < 0:  # sign constraint: losing starting linemen cannot help a team -> drop the term, refit steam alone
        O, S = 0.0, np.linalg.lstsq(np.column_stack([-tr.es]), y, rcond=None)[0][0]
    st = tr[tr.tot.notna()]
    OT, ST = np.linalg.lstsq(np.column_stack([st.ol_s, st.et]), st.tot - st.bt, rcond=None)[0]
    if OT > 0:  # banged-up lines cannot raise scoring
        OT, ST = 0.0, np.linalg.lstsq(np.column_stack([st.et]), st.tot - st.bt, rcond=None)[0][0]
    p = {"O": round(float(O), 3), "OT": round(float(OT), 3), "S": round(float(S), 3), "ST": round(float(ST), 3)}
    m = tr.bm + p["O"] * tr.ol_d - p["S"] * tr.es
    rm = ((tr.margin - m) * np.where(m >= 0, 1.0, -1.0)).to_numpy()
    rt = (st.tot - (st.bt + p["OT"] * st.ol_s + p["ST"] * st.et)).to_numpy()
    p["skew_a"] = round(float(skewnorm.fit(rm)[0]), 4)
    p["sd_m"] = NI.SD_MARGIN
    p["fitted_sd_m"] = round(float(rm.std()), 3)
    p["skew_t"] = [round(float(v), 4) for v in skewnorm.fit(rt)]
    p["n_train"] = int(len(tr))
    return p


def grade(g: pd.DataFrame) -> dict:
    out = {}
    ar = g.margin + g.spread
    sp = g.spread.notna() & (ar != 0)
    orr = g.tot - g.total
    to = g.total.notna() & (orr != 0)
    has = sp & (g.margin != 0)
    y = (g.margin > 0).astype(float)
    out["market_fav_ml"] = acc(((g.spread < 0) == (g.margin > 0))[has & (g.spread != 0)])
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
    out["inst_ats_when_steam"] = rate(((g.ipc > 0.5) == (ar > 0))[k])
    k = to & (g.et.abs() >= 0.5)
    out["steam_follow_ou"] = rate(((g.et > 0) == (orr > 0))[k])
    k = sp & (g.ol_d.abs() >= 0.75)  # one side missing ~a starter more: back the healthier line
    out["ol_healthier_ats"] = rate(((g.ol_d > 0) == (ar > 0))[k])
    out["inst_ats_when_ol_gap"] = rate(((g.ipc > 0.5) == (ar > 0))[k])
    k = to & (g.ol_s >= 1.5)
    out["ol_banged_under"] = rate((orr < 0)[k])
    out["brier_engine"] = round(float(((g.ph - y) ** 2)[has].mean()), 4)
    out["brier_inst"] = round(float(((g.iph - y) ** 2)[has].mean()), 4)
    sd = 13.5
    from math import erf, sqrt
    mk = np.array([0.5 * (1 + erf(-s / (sd * sqrt(2)))) for s in g.spread.fillna(0)])
    out["brier_market_spread"] = round(float(((mk - y) ** 2)[has].mean()), 4)
    out["margin_mae_engine"] = round(float((g.bm - g.margin).abs()[sp].mean()), 2)
    out["margin_mae_inst"] = round(float((g.im - g.margin).abs()[sp].mean()), 2)
    out["margin_mae_line"] = round(float((-g.spread - g.margin).abs()[sp].mean()), 2)
    out["with_opener"] = int(g.s_open.notna().sum())
    out["with_ol_both"] = int((g.lost_h.notna() & g.lost_a.notna()).sum())
    return out


def run(state: Path, first_test: int = 2023):
    df = load(state).dropna(subset=["bm", "bt", "margin", "tot"]).reset_index(drop=True)
    rows, fits = [], {}
    for s in sorted(df.season.unique()):
        if s < first_test:
            continue
        tr, te = df[df.season < s], df[df.season == s].copy()
        p = fit(tr)
        fits[int(s)] = p
        res = [NI.nfl_institutional_simulation(r.bm, r.bt, r.lost_h, r.lost_a, r.spread, r.total, None, None,
                                               p, n=4000, seed=i) for i, r in enumerate(te.itertuples())]
        # steam measured open -> close on Bovada, applied on top of the graded (closing) line
        te["im"] = te.bm + p["O"] * te.ol_d - p["S"] * te.es
        te["it"] = te.bt + p["OT"] * te.ol_s + p["ST"] * te.et
        res = [NI.simulate(m, t, p, sp_, ln, n=4000, seed=i)
               for i, (m, t, sp_, ln) in enumerate(zip(te.im, te.it, te.spread, te.total))]
        te["iph"] = [x["p_home"] for x in res]
        te["ipc"] = [x.get("p_home_cover", np.nan) for x in res]
        te["ipo"] = [x.get("p_over", np.nan) for x in res]
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
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 2023
    rep, g = run(state, first)
    (state / NI.REPORT).write_text(json.dumps(rep, indent=1, default=str))
    g.to_csv(state / "reports" / "nfl_institutional_games.csv", index=False)
    print(json.dumps({k: rep[k] for k in ("by_season", "window_3wk", "all_tests", "latest_fit")}, indent=1, default=str))
