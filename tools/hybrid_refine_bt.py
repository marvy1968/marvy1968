"""Walk-forward optimizer for the REFINED hybrid engine (marv/hybrid.py v2): Hybrid H2H matrix + Trend Catcher +
Dynamic Velocity Decay + Restricted Monte Carlo totals. NFL, CFB, WNBA (PAPER research; nothing here changes picks).

Refinements tested (all hyper-parameters picked on EARLIER seasons only, then graded on the next season):
  velocity decay   level_c = season_c + A * d / (1 + |d| / (V0 * sd_c)),  d = last3_c - season_c
                   (sd_c = league per-game sd of the category, earlier seasons). A = trend weight, V0 = decay scale:
                   small swings pass through, big swings saturate (the pasted 1/(1+|delta|) idea, scale-free).
  shrinkage        level -> (n * level + N0 * league mean) / (n + N0)   (early-season regression)
  trend catcher    TOV spike / reb-margin (football ypp-margin) drop, both velocity-decayed, floor 0.80
  H2H side         margin = HFA + K * category-point diff + E * efficiency margin (pace/2 x ppp gap), lstsq on train
  restricted MC    pace x points-per-possession only; opponent combine additive or multiplicative (M);
                   calibrated total = a + b * MC mean (fit on train). Side never uses MC.
Bets graded at the closing/consensus line, -110. Bar = marv/proven.py + tools/hybrid_bt.passes (n>=100, ROI>0,
>52.4% in most seasons with 10+ bets). Thresholds are fixed in advance (edge/gap 0, 3, 7) plus a nested top-20% rule
whose cut-off comes from earlier seasons' out-of-sample edges only.
Usage: nice .venv/bin/python tools/hybrid_refine_bt.py [nfl|cfb|wnba|all] [OUTDIR]
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from marv import hybrid as H  # noqa: E402
import hybrid_bt as HB  # noqa: E402

CACHE = HB.CACHE
BE = 0.5238
GRID_A = (0.0, 0.25, 0.5, 1.0, 1.2)
GRID_V0 = (0.5, 1.0, 2.0, 1e9)
GRID_N0 = (0.0, 2.0, 4.0, 8.0)
GRID_M = (0, 1)
GAPS = (0, 3, 7)


def load(sport):
    m = HB.games(sport)
    if sport == "wnba":
        f = CACHE / "wnba_lines_hist.csv"
        if f.exists():
            L = pd.read_csv(f, dtype={"game_id": str})[["game_id", "spread", "total"]]
            m = m.drop(columns=["spread", "total"]).merge(L, on="game_id", how="left")
    m["margin"] = m.home_points - m.away_points
    m["tot"] = m.home_points + m.away_points
    return m.reset_index(drop=True)


def league_stats(tg_train):
    return {c: (float(tg_train[c].mean()), float(tg_train[c].std())) for c in [*H.CATS, "poss"]}


def team_games(sport):
    if sport == "cfb":
        return H.cfb_team_games(CACHE, range(2018, 2027))
    if sport == "nfl":
        return H.nfl_team_games(CACHE, range(2020, 2027))
    return H.bb_team_games(CACHE, sport, HB.BB_SEASONS[sport])


def levels(m, side, lg, A, V0, N0):
    n = m[f"{side}n"].to_numpy(float)
    out = {}
    for c in H.CATS:
        s, l3 = m[f"{side}{c}"].to_numpy(float), m[f"{side}{c}3"].to_numpy(float)
        mu, sd = lg[c]
        d = l3 - s
        lv = s + A * d / (1 + np.abs(d) / (V0 * max(sd, 1e-9)))
        out[c] = (n * lv + N0 * mu) / (n + N0) if N0 else lv
    p = m[f"{side}poss"].to_numpy(float)
    out["poss"] = (n * p + N0 * lg["poss"][0]) / (n + N0) if N0 else p
    return out


def trend_mod(m, side, lg, sport, V0):
    tov, tov3 = m[f"{side}tov"].to_numpy(float), m[f"{side}tov3"].to_numpy(float)
    y, y3 = m[f"{side}yppm"].to_numpy(float), m[f"{side}yppm3"].to_numpy(float)
    spike, drop = np.maximum(0, tov3 - tov), np.maximum(0, y - y3)
    spike = spike / (1 + spike / (V0 * lg["tov"][1]))
    drop = drop / (1 + drop / (V0 * lg["yppm"][1]))
    return np.maximum(H.MOD_FLOOR, 1 - H.TOV_K[sport] * spike - H.REB_K[sport] * drop)


def compute(m, sport, lg, A, V0, N0, M, pace_dir):
    h, a = levels(m, "h_", lg, A, V0, N0), levels(m, "a_", lg, A, V0, N0)
    mh, ma = trend_mod(m, "h_", lg, sport, V0), trend_mod(m, "a_", lg, sport, V0)
    hv, av = dict(h), dict(a)
    hv["off"], av["off"] = h["off"] * mh, a["off"] * ma
    hv["def"], av["def"] = h["def"] / mh, a["def"] / ma
    hp = np.zeros(len(m)); ap = np.zeros(len(m))
    for c in H.CATS:
        d = hv[c] - av[c]
        if c in H.LOWER_BETTER:
            d = -d
        if c == "pace":
            d = d * pace_dir
        hp += np.where(d > 0, mh, 0); ap += np.where(d < 0, ma, 0)
    diff = hp - ap
    pace = (h["poss"] + a["poss"]) / 2
    lo = lg["off"][0]
    if M:
        eh, ea = hv["off"] * av["def"] / lo, av["off"] * hv["def"] / lo
    else:
        eh, ea = (hv["off"] + av["def"]) / 2, (av["off"] + hv["def"]) / 2
    return diff, pace / 2 * (eh - ea), pace / 2 * (eh + ea)


def fit_side(m, diff, effm):
    X = np.column_stack([(~m.neutral.astype(bool)).astype(float), diff, effm])
    ok = np.isfinite(X).all(1) & m.margin.notna().to_numpy()
    coef = np.linalg.lstsq(X[ok], m.margin.to_numpy()[ok], rcond=None)[0]
    pred = X @ coef
    return coef, pred, float(np.nanmean(np.abs(pred[ok] - m.margin.to_numpy()[ok])))


def fit_tot(m, mc):
    ok = np.isfinite(mc) & m.tot.notna().to_numpy()
    X = np.column_stack([np.ones(len(m)), mc])
    coef = np.linalg.lstsq(X[ok], m.tot.to_numpy()[ok], rcond=None)[0]
    pred = X @ coef
    return coef, pred, float(np.nanmean(np.abs(pred[ok] - m.tot.to_numpy()[ok])))


def rate(ok):
    n = len(ok); w = int(np.sum(ok))
    return {"n": n, "hit": round(w / n, 4) if n else None, "roi": round((w * 100 / 110 - (n - w)) / n, 4) if n else None}


def grade_bets(df, edge_col, ok_col, push_col, lo, extra=None):
    mk = df[edge_col].notna() & ~df[push_col] & (df[edge_col].abs() >= lo)
    if extra is not None:
        mk &= extra
    r = rate(df.loc[mk, ok_col].to_numpy())
    r["seasons"] = {int(s): rate(d[ok_col].to_numpy()) for s, d in df[mk].groupby("season")}
    return r


def run(sport):
    m = load(sport)
    tg = team_games(sport)
    seasons = sorted(m.season.unique())
    rows, fits = [], {}
    prev_edges = {"ats": [], "ou": []}
    for s in seasons[1:]:
        tr, te = m[m.season < s].reset_index(drop=True), m[m.season == s].reset_index(drop=True)
        if len(tr) < 100 or te.empty:
            continue
        lg = league_stats(tg[tg.season < s])
        agree = float(np.mean(np.sign(tr.h_pace - tr.a_pace) * np.sign(tr.margin)))
        pace_dir = int(np.sign(agree)) if abs(agree) > 0.02 else 0
        best_s, best_t = None, None
        for A, V0, N0 in itertools.product(GRID_A, GRID_V0, GRID_N0):
            for M in GRID_M:
                diff, effm, mc = compute(tr, sport, lg, A, V0, N0, M, pace_dir)
                if M == 0:
                    cs, _, es = fit_side(tr, diff, effm)
                    if best_s is None or es < best_s[0]:
                        best_s = (es, A, V0, N0, cs)
                ct, _, et = fit_tot(tr, mc)
                if best_t is None or et < best_t[0]:
                    best_t = (et, A, V0, N0, M, ct)
        # baseline = the Oct 9 engine's spirit: no decay, no shrink (A=0 uses season means), matrix only
        _, A, V0, N0, cs = best_s
        diff, effm, _ = compute(te, sport, lg, A, V0, N0, 0, pace_dir)
        proj = np.column_stack([(~te.neutral.astype(bool)).astype(float), diff, effm]) @ cs
        _, At, Vt, Nt, Mt, ct = best_t
        _, _, mc = compute(te, sport, lg, At, Vt, Nt, Mt, pace_dir)
        tot_hat = ct[0] + ct[1] * mc
        fits[int(s)] = {"side": {"A": A, "V0": V0, "N0": N0, "HFA": round(cs[0], 2), "K": round(cs[1], 3), "E": round(cs[2], 3),
                                 "train_mae": round(best_s[0], 3)},
                        "tot": {"A": At, "V0": Vt, "N0": Nt, "M": Mt, "a": round(ct[0], 2), "b": round(ct[1], 3),
                                "train_mae": round(best_t[0], 3)}, "pace_dir": pace_dir}
        # nested top-20% thresholds from earlier seasons' OOS edges
        thr_ats = float(np.quantile(prev_edges["ats"], 0.8)) if len(prev_edges["ats"]) >= 200 else np.nan
        thr_ou = float(np.quantile(prev_edges["ou"], 0.8)) if len(prev_edges["ou"]) >= 200 else np.nan
        d = pd.DataFrame({"game_id": te.game_id, "season": int(s), "margin": te.margin, "tot": te.tot, "spread": te.spread,
                          "total": te.total, "proj": proj, "tot_hat": tot_hat, "diff": diff,
                          "thr_ats": thr_ats, "thr_ou": thr_ou})
        rows.append(d)
        e1 = (d.proj + d.spread).abs().dropna(); e2 = (d.tot_hat - d.total).abs().dropna()
        prev_edges["ats"] += list(e1); prev_edges["ou"] += list(e2)
        print(sport, s, fits[int(s)], flush=True)
    df = pd.concat(rows, ignore_index=True)
    r = {"fits": fits}
    dec = df.margin != 0
    r["ml_all"] = rate(((df.proj > 0) == (df.margin > 0))[dec].to_numpy())
    has = df.spread.notna() & (df.spread != 0) & dec
    r["market_fav_ml"] = rate(((df.spread < 0) == (df.margin > 0))[has].to_numpy())
    r["ml_same_games"] = rate(((df.proj > 0) == (df.margin > 0))[has].to_numpy())
    r["margin_mae"] = round(float((df.proj - df.margin).abs()[df.spread.notna()].mean()), 2)
    r["line_margin_mae"] = round(float((-df.spread - df.margin).abs().mean()), 2)
    r["total_mae"] = round(float((df.tot_hat - df.tot).abs()[df.total.notna()].mean()), 2)
    r["line_total_mae"] = round(float((df.total - df.tot).abs().mean()), 2)
    ar = df.margin + df.spread
    df["ats_edge"] = np.where(df.spread.notna(), df.proj + df.spread, np.nan)
    df["ats_ok"] = (df.ats_edge > 0) == (ar > 0)
    df["ats_push"] = (ar == 0) | (df.ats_edge == 0)
    orr = df.tot - df.total
    df["ou_edge"] = np.where(df.total.notna(), df.tot_hat - df.total, np.nan)
    df["ou_ok"] = (df.ou_edge > 0) == (orr > 0)
    df["ou_push"] = (orr == 0) | (df.ou_edge == 0)
    for lo in GAPS:
        r[f"ats_edge{lo}"] = grade_bets(df, "ats_edge", "ats_ok", "ats_push", lo)
        r[f"ou_gap{lo}"] = grade_bets(df, "ou_edge", "ou_ok", "ou_push", lo)
    r["ats_nested_top20"] = grade_bets(df, "ats_edge", "ats_ok", "ats_push", 0, df.ats_edge.abs() >= df.thr_ats)
    r["ou_nested_top20"] = grade_bets(df, "ou_edge", "ou_ok", "ou_push", 0, df.ou_edge.abs() >= df.thr_ou)
    for side in ("over", "under"):
        sel = (df.ou_edge > 0) if side == "over" else (df.ou_edge < 0)
        r[f"ou_nested_top20_{side}"] = grade_bets(df, "ou_edge", "ou_ok", "ou_push", 0, (df.ou_edge.abs() >= df.thr_ou) & sel)
    r["proven"] = {k: HB.passes(v) for k, v in r.items() if isinstance(v, dict) and "seasons" in v}
    return r, df


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/opt/marv-bot/state/reports")
    path = out / "hybrid_refine_backtest.json"
    try:
        rep = json.loads(path.read_text())
    except (OSError, ValueError):
        rep = {}
    for sp in (["nfl", "cfb", "wnba"] if which == "all" else [which]):
        r, df = run(sp)
        df.to_csv(out / f"hybrid_refine_{sp}_games.csv", index=False)
        rep[sp] = r
        path.write_text(json.dumps(rep, indent=1, default=str))
        print(sp, json.dumps({k: v for k, v in r.items() if k != "fits"}, default=str), flush=True)
