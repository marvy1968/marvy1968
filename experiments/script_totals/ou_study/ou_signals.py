"""Lean O/U extremes signal study (paper/tracking only). Reuses cached nflverse / CFB / WNBA data (no downloads).
Target per game: y = actual total - CLOSING total. High Shootout = y >= TH, Defensive Grind = y <= -TH (TH 3 NFL/CFB, 4 WNBA).
All features are pregame and walk-forward (each team's prior games only). Time split: train early seasons, test later unseen seasons.
CURRENT = Script O/U / Master extremes proxy (bt_totals.py): proj = team off/def 400-day averages, |proj-close| >= TH, UF=1, EV>0.
Usage: python ou_signals.py [cache_dir]  -> ou_report.json + ou_games_<sport>.csv"""
import sys, json, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from math import erf, sqrt
C = (sys.argv[1] if len(sys.argv) > 1 else "/opt/marv-bot/state/cache").rstrip("/") + "/"
TH = dict(nfl=3.0, cfb=3.0, wnba=4.0); SD = dict(nfl=13.0, cfb=16.0, wnba=15.0); BLOW = dict(nfl=17, cfb=21, wnba=20)
SPLIT = dict(nfl=2024, cfb=2023, wnba=2024)   # first TEST season (train = everything before)
FIRST = dict(nfl=2020, cfb=2018, wnba=2018)   # first season scored (earlier seasons only feed the 400-day history)

def pay(a): return a/100 if a > 0 else 100/-a
def ncdf(x): return 0.5*(1+erf(x/sqrt(2)))

# ---------------------------------------------------------------- loaders -> one row per game
def load_nfl():
    g = pd.read_csv(C+"nflverse_games.csv"); g = g[g.season >= 2018].copy(); g["date"] = pd.to_datetime(g.gameday)
    st = pd.concat([pd.read_csv(C+f"nfl_stats_team_week_{s}.csv") for s in range(2020, 2027)])
    st["plays"] = st.attempts+st.carries+st.sacks_suffered
    inj = []
    for s in range(2020, 2027):
        try:
            i = pd.read_csv(C+f"nfl_injuries_{s}.csv", low_memory=False)
            q = i[i.report_status.isin(["Questionable", "Doubtful", "Out"])].groupby(["team", "week"]).size()
            inj.append(pd.DataFrame({"season": s, "team": [k[0] for k in q.index], "week": [k[1] for k in q.index], "n": q.values, "thr": q.quantile(0.85)}))
        except Exception: pass
    inj = pd.concat(inj); inj["hit"] = inj.n >= inj.thr; ih = {(r.season, r.team, r.week): r.hit for r in inj.itertuples()}
    df = pd.DataFrame({"gid": g.game_id, "season": g.season, "date": g.date, "home": g.home_team, "away": g.away_team,
                       "hs": g.home_score, "as_": g.away_score, "close": g.total_line, "spread": g.spread_line,
                       "po": g.over_odds.fillna(-110), "pu": g.under_odds.fillna(-110), "open": np.nan,
                       "rival": g.div_game.eq(1), "short_rest": (g.home_rest.fillna(7) <= 4) | (g.away_rest.fillna(7) <= 4),
                       "inj": [ih.get((s, h, w), False) or ih.get((s, a, w), False) for s, h, a, w in zip(g.season, g.home_team, g.away_team, g.week)],
                       "outdoor": g.roof.isin(["outdoors", "open"]).astype(float), "wind": np.where(g.roof.isin(["outdoors", "open"]), g.wind.fillna(g.wind.median()), 0.0),
                       "temp": np.where(g.roof.isin(["outdoors", "open"]), g.temp.fillna(g.temp.median()), 70.0)})
    pl = st[["game_id", "team", "plays"]].rename(columns={"game_id": "gid"})
    return df, pl

def load_cfb():
    g = pd.concat([pd.read_parquet(C+f"cfb_games_{s}.parquet") for s in range(2018, 2027)])
    sch = pd.concat([pd.read_parquet(C+f"cfb_sched_{s}.parquet") for s in range(2018, 2027)])[["game_id", "conference_game"]]
    g["game_id"] = g.game_id.astype(str); sch["game_id"] = sch.game_id.astype(str)
    g = g.merge(sch.drop_duplicates("game_id"), on="game_id", how="left"); g = g[g.home_fbs & g.away_fbs].copy(); g["date"] = pd.to_datetime(g.date)
    o = pd.read_csv(C+"cfb_lines_oc.csv", dtype={"game_id": str}).drop_duplicates("game_id")[["game_id", "total_open", "total"]].rename(columns={"total": "tc"})
    g = g.merge(o, on="game_id", how="left")
    tg = pd.concat([pd.read_parquet(C+f"cfb_teamgames_{s}.parquet") for s in range(2018, 2027)]).drop_duplicates(["game_id", "team"])
    tg["game_id"] = tg.game_id.astype(str)
    df = pd.DataFrame({"gid": g.game_id, "season": g.season, "date": g.date, "home": g.home, "away": g.away, "hs": g.home_points, "as_": g.away_points,
                       "close": g.tc.fillna(g.total), "spread": g.spread, "po": -110.0, "pu": -110.0, "open": g.total_open,
                       "rival": g.conference_game.fillna(False).astype(bool) & (g.spread.abs() <= 7), "short_rest": False, "inj": False})
    pl = tg[["game_id", "team", "plays"]].rename(columns={"game_id": "gid"})
    return df, pl

def load_wnba():
    b = pd.concat([pd.read_parquet(C+f"wnba_box_{s}.parquet") for s in range(2018, 2027)])
    for c in ("field_goals_attempted", "offensive_rebounds", "total_turnovers", "free_throws_attempted", "team_score", "opponent_team_score"):
        b[c] = pd.to_numeric(b[c], errors="coerce")
    b["date"] = pd.to_datetime(b.game_date); b["gid"] = b.game_id.astype(str)
    b["plays"] = b.field_goals_attempted - b.offensive_rebounds + b.total_turnovers + 0.44*b.free_throws_attempted  # possessions
    L = pd.read_csv(C+"wnba_lines_hist.csv", dtype={"game_id": str}).drop_duplicates("game_id")
    O = pd.read_csv(C+"wnba_open_lines.csv", dtype={"game_id": str}).drop_duplicates("game_id")[["game_id", "t_open", "t_close"]]
    h = b[b.team_home_away == "home"][["gid", "season", "date", "team_display_name", "team_score", "opponent_team_display_name", "opponent_team_score", "season_type"]]
    h.columns = ["gid", "season", "date", "home", "hs", "away", "as_", "stype"]
    g = h.drop_duplicates("gid").merge(L.drop(columns=["season"]), left_on="gid", right_on="game_id").merge(O, on="game_id", how="left").sort_values("date")
    met = [((g.date < d) & (g.date >= d-pd.Timedelta(days=4)) & (((g.home == x) & (g.away == y)) | ((g.home == y) & (g.away == x)))).any()
           for d, x, y in zip(g.date, g.home, g.away)]
    df = pd.DataFrame({"gid": g.gid, "season": g.season, "date": g.date, "home": g.home, "away": g.away, "hs": g.hs, "as_": g.as_,
                       "close": g.t_close.fillna(g.total), "spread": g.spread, "po": -110.0, "pu": -110.0, "open": g.t_open,
                       "rival": np.array(met) & (g.stype.values == 3) & (g.spread.abs().values <= 4), "short_rest": False, "inj": False})
    pl = b[["gid", "team_display_name", "plays"]].rename(columns={"team_display_name": "team"})
    return df, pl

# ---------------------------------------------------------------- walk-forward features
def build(sp):
    df, pl = dict(nfl=load_nfl, cfb=load_cfb, wnba=load_wnba)[sp]()
    df = df.dropna(subset=["hs", "as_", "close"]).sort_values("date").reset_index(drop=True); df["gid"] = df.gid.astype(str)
    pl["gid"] = pl.gid.astype(str); pl = pl.drop_duplicates(["gid", "team"])
    df["tot"] = df.hs+df.as_; df["y"] = df.tot-df.close; df["over"] = np.sign(df.y)
    lg = []
    for side, o in (("home", "away"), ("away", "home")):
        pf, pa = ("hs", "as_") if side == "home" else ("as_", "hs")
        lg.append(pd.DataFrame({"gid": df.gid, "date": df.date, "season": df.season, "team": df[side], "pf": df[pf], "pa": df[pa],
                                "y": df.y, "ov": (df.y > 0).astype(float).where(df.y != 0), "margin": df[pf]-df[pa], "side": side}))
    lg = pd.concat(lg).merge(pl, on=["gid", "team"], how="left").sort_values(["team", "date"]).reset_index(drop=True)
    out = []
    for t, d in lg.groupby("team", sort=False):
        d = d.set_index("date"); r = {}
        w = d[["pf", "pa"]].rolling("400D", closed="left")
        r["off"] = w.mean().pf.values; r["dfn"] = w.mean().pa.values; r["n400"] = w.count().pf.values
        s = d[["y", "ov", "plays", "margin"]].shift(1)
        r["ou5"] = s.y.rolling(5, min_periods=3).mean().values        # team O/U trend: avg (total - close) last 5
        r["ovr10"] = s.ov.rolling(10, min_periods=5).mean().values    # team O/U trend: over hit-rate last 10
        r["pace"] = s.plays.rolling(8, min_periods=3).mean().values   # plays / possessions per game, last 8
        r["last_m"] = s.margin.values
        out.append(pd.DataFrame(r).assign(gid=d.gid.values, team=t))
    F = pd.concat(out)
    # league per-team scoring average over the same 400-day window (strictly before the date)
    day = df.groupby("date").agg(pts=("tot", "sum"), n=("tot", "size")); day["pts"] /= 1; day["n"] *= 2
    cs = day.rolling("400D", closed="left").sum(); lgavg = (cs.pts/cs.n).rename("lg")
    df = df.merge(lgavg, left_on="date", right_index=True, how="left")
    lgp = lg.groupby("date").plays.mean().rolling("365D", closed="left").mean().rename("lgpace")
    df = df.merge(lgp, left_on="date", right_index=True, how="left")
    for side in ("home", "away"):
        df = df.merge(F.rename(columns={c: f"{side[0]}_{c}" for c in F.columns if c not in ("gid", "team")}).rename(columns={"team": side}),
                      on=["gid", side], how="left")
    df = df[(df.h_n400 >= 3) & (df.a_n400 >= 3)].copy()
    df["proj"] = df.h_off+df.h_dfn+df.a_off+df.a_dfn-2*df.lg     # == bt_totals proj (hfa cancels in the total)
    df["d"] = df.proj-df.close
    # simple candidate signals (all pregame)
    df["f_d"] = df.d                                               # Marv proxy total vs closing line
    df["f_offdef"] = (df.h_off+df.a_dfn)-(df.a_off+df.h_dfn)*0 - df.close/2   # home side implied vs half line (noise check)
    df["f_pace"] = (df.h_pace+df.a_pace)/2-df.lgpace                # pace vs league
    df["f_trend_ou5"] = df.h_ou5+df.a_ou5                          # team O/U trend (points over/under line, last 5)
    df["f_trend_ovr10"] = (df.h_ovr10+df.a_ovr10)/2-0.5            # team O/U trend (over hit-rate last 10)
    lc = df.groupby("date").close.mean().rolling("365D", closed="left").mean().rename("lgclose")
    df = df.merge(lc, left_on="date", right_index=True, how="left")
    df["f_close"] = df.close-df.lgclose                            # line level vs prior-365-day average line (no leakage)
    df["f_absspread"] = df.spread.abs()
    df["f_move"] = df.close-df.open                                # open -> close move (CFB 2021+, WNBA 2020+)
    if sp == "nfl": df["f_wind"] = df.wind; df["f_temp"] = df.temp; df["f_outdoor"] = df.outdoor
    df = df.drop(columns=["f_offdef"])
    emo = (df.h_last_m.fillna(0) <= -BLOW[sp]) | (df.a_last_m.fillna(0) <= -BLOW[sp]) | df.short_rest.astype(bool)
    df["uf"] = (~(df.rival.astype(bool) | emo | df.inj.astype(bool))).astype(int)
    mu = 0.5*df.proj+0.5*df.close; pov = 1-np.array([ncdf(x) for x in (df.close-mu)/SD[sp]])
    df["cur_side"] = np.sign(df.d); p = np.where(df.cur_side > 0, pov, 1-pov)
    pr = np.where(df.cur_side > 0, df.po, df.pu); df["cur_ev"] = p*np.array([pay(a) for a in pr])-(1-p)
    df["cur"] = ((df.d.abs() >= TH[sp]) & (df.uf == 1) & (df.cur_ev > 0)).astype(int)   # CURRENT extremes rule
    return df[df.season >= FIRST[sp]].reset_index(drop=True)

# ---------------------------------------------------------------- evaluation helpers
def grade(D, side):
    """side: +1 over, -1 under, 0 no bet. Returns record at actual prices (NFL) or -110."""
    m = side != 0; X = D[m]; s = side[m]
    if len(X) == 0: return dict(n=0)
    r = np.sign(X.y.values)*s; pr = np.where(s > 0, X.po.values, X.pu.values)
    u = np.where(r > 0, [pay(a) for a in pr], np.where(r < 0, -1.0, 0.0))
    W, L = int((r > 0).sum()), int((r < 0).sum())
    return dict(n=int(len(X)), W=W, L=L, P=int((r == 0).sum()), hit=round(W/max(W+L, 1), 3), units=round(float(u.sum()), 2), roi=round(float(u.sum()/len(X)), 3),
                overs=int((s > 0).sum()), unders=int((s < 0).sum()))

def auc(x, lab):
    ok = ~np.isnan(x); x, lab = x[ok], lab[ok]
    if lab.sum() < 10 or (1-lab).sum() < 10: return None
    r = pd.Series(x).rank().values; n1 = lab.sum(); n0 = len(lab)-n1
    return float((r[lab == 1].sum()-n1*(n1+1)/2)/(n1*n0))

def study(sp):
    D = build(sp); th = TH[sp]; tr, te = D[D.season < SPLIT[sp]], D[D.season >= SPLIT[sp]]
    feats = [c for c in D.columns if c.startswith("f_")]
    rep = dict(sport=sp, n_train=len(tr), n_test=len(te), train_seasons=f"{int(tr.season.min())}-{int(tr.season.max())}",
               test_seasons=f"{int(te.season.min())}-{int(te.season.max())}", test_last_date=str(te.date.max().date()),
               extremes_rate_train=dict(shootout=round(float((tr.y >= th).mean()), 3), grind=round(float((tr.y <= -th).mean()), 3)))
    # 1) separation: AUC High Shootout (y>=TH) vs Defensive Grind (y<=-TH), train & test; corr with y
    sep = {}
    for f in feats:
        a = {}
        for nm, X in (("train", tr), ("test", te)):
            E = X[(X.y >= th) | (X.y <= -th)]
            a[nm+"_auc"] = None if E[f].notna().sum() < 50 else round(auc(E[f].values.astype(float), (E.y >= th).values.astype(int)), 3)
            a[nm+"_corr"] = None if X[f].notna().sum() < 50 else round(float(X[[f, "y"]].dropna().corr().iloc[0, 1]), 3)
            a[nm+"_n"] = int(E[f].notna().sum())
        sep[f] = a
    rep["separation"] = sep
    # 2) pick top signals on TRAIN only (|AUC-.5|), max 3, needs >= 60% coverage in train and test
    cov = {f: min(tr[f].notna().mean(), te[f].notna().mean()) for f in feats}
    ranked = sorted([f for f in feats if sep[f]["train_auc"] is not None and cov[f] >= 0.6], key=lambda f: -abs(sep[f]["train_auc"]-0.5))
    rep["train_rank"] = [(f, sep[f]["train_auc"]) for f in ranked]
    def fit(fs):
        X = tr[fs+["y"]].dropna(); mu, sd = X[fs].mean(), X[fs].std()
        Z = np.c_[np.ones(len(X)), ((X[fs]-mu)/sd).values]; beta = np.linalg.lstsq(Z, X.y.values, rcond=None)[0]
        def pred(Y): return beta[0]+(((Y[fs]-mu)/sd).fillna(0).values @ beta[1:])
        return pred, dict(zip(["intercept"]+fs, [round(float(b), 3) for b in beta])), mu, sd
    def pick_thr(pred_tr, base_mask_tr, side_fn):
        best = None
        for k in np.arange(0.25, 6.01, 0.25):
            s = side_fn(pred_tr, k) * base_mask_tr
            g = grade(tr, s)
            if g["n"] < max(60, 0.04*len(tr)): continue
            sc = g["roi"]-1/np.sqrt(g["n"])
            if best is None or sc > best[0]: best = (sc, k)
        return None if best is None else best[1]
    cur_tr, cur_te = (tr.cur.values*tr.cur_side.values).astype(int), (te.cur.values*te.cur_side.values).astype(int)
    rep["CURRENT"] = dict(rule=f"|proj-close|>={th}, UF=1, EV>0", train=grade(tr, cur_tr), test=grade(te, cur_te),
                          test_over=grade(te, np.where(cur_te > 0, 1, 0)), test_under=grade(te, np.where(cur_te < 0, -1, 0)))
    variants = {}
    sidef = lambda p, k: np.where(p >= k, 1, np.where(p <= -k, -1, 0))
    from itertools import combinations
    def evalc(fs):
        pred, coef, mu, sd = fit(list(fs)); ptr, pte = pred(tr), pred(te); k = pick_thr(ptr, 1, sidef)
        if k is None: return None
        gtr = grade(tr, sidef(ptr, k)); sc = gtr["roi"]-1/np.sqrt(gtr["n"])
        v = dict(signals=list(fs), coef_pts_per_sd=coef, thr_pts=float(k), train_score=round(float(sc), 4), train=gtr, test=grade(te, sidef(pte, k)),
                 cur_and_agree=dict(train=grade(tr, np.where(ptr*cur_tr > 0, cur_tr, 0)), test=grade(te, np.where(pte*cur_te > 0, cur_te, 0))),
                 by_test_season={int(s_): grade(te[te.season == s_], sidef(pte, k)[te.season.values == s_]) for s_ in sorted(te.season.unique())})
        return v
    allc = []
    for r_ in (1, 2, 3):
        for fs in combinations(ranked, r_):
            v = evalc(fs)
            if v: allc.append(v)
    allc.sort(key=lambda v: -v["train_score"])
    nt = [v for v in allc if not any(f.startswith("f_trend") for f in v["signals"])]
    variants["BEST_any(train-selected)"] = allc[0]
    variants["BEST_no_trend(train-selected)"] = nt[0]
    variants["top5_by_train"] = [(v["signals"], v["train_score"], v["train"]["roi"], v["train"]["n"], v["test"]["roi"], v["test"]["n"]) for v in allc[:5]]
    rep["CURRENT"]["by_test_season"] = {int(s_): grade(te[te.season == s_], cur_te[te.season.values == s_]) for s_ in sorted(te.season.unique())}
    # 3) team O/U trend alone, and as filter on CURRENT
    for f in ("f_trend_ou5", "f_trend_ovr10"):
        a_tr, a_te = tr[f].fillna(0).values, te[f].fillna(0).values
        variants["CUR+"+f+"_agree"] = dict(train=grade(tr, np.where(a_tr*cur_tr > 0, cur_tr, 0)), test=grade(te, np.where(a_te*cur_te > 0, cur_te, 0)),
                                         disagree_test=grade(te, np.where(a_te*cur_te < 0, cur_te, 0)))
        # standalone follow-the-trend at the top/bottom 20% (thresholds from train)
        q_lo, q_hi = np.nanquantile(tr[f], 0.2), np.nanquantile(tr[f], 0.8)
        st = lambda X: np.where(X[f] >= q_hi, 1, np.where(X[f] <= q_lo, -1, 0))
        variants["TREND_only_"+f] = dict(q=(round(float(q_lo), 3), round(float(q_hi), 3)), train=grade(tr, st(tr)), test=grade(te, st(te)),
                                         fade_test=grade(te, -st(te)))
    rep["variants"] = variants
    D.to_csv(f"ou_games_{sp}.csv", index=False)
    return rep

if __name__ == "__main__":
    R = {}
    for sp in (sys.argv[2].split(",") if len(sys.argv) > 2 else ("nfl", "cfb", "wnba")):
        try: R[sp] = study(sp); print(sp, "done", R[sp]["n_train"], R[sp]["n_test"], flush=True)
        except Exception as ex:
            import traceback; traceback.print_exc(); R[sp] = dict(error=str(ex))
    json.dump(R, open("ou_report.json", "w"), indent=1, default=str)
