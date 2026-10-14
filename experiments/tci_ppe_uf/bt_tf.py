"""TCI/PPE/UF paper experiment (NOT wired live). Walk-forward Sep 21 - Oct 12 2026.
P_MC: 5,000-iteration Monte Carlo on a season-to-date power rating blended 50/50 with the market (Marv-style shrink).
New: logit(P_final) = logit(P_MC) + K*(zTCI) + K*(zPPE)  (odds multipliers exp(K*z), =1.0 at z=0; K fixed, no fitting).
UF: 0 -> no bet (rivalry chaos / emotional spot / late-injury volatility). Bets: 1u, edge >= 3% over break-even."""
import numpy as np, pandas as pd, glob, sys, json, warnings; warnings.filterwarnings("ignore")
C = "/opt/marv-bot/state/cache/"; sys.path.insert(0, "/opt/marv-bot")
T0, T1 = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2026-09-21"), pd.Timestamp("2026-10-13")
TFMAX, TFN = 0.025, 4
K, EDGE, N = 0.10, 0.03, 5000
rng = np.random.default_rng(7)

def be(am): return 100/(am+100) if am > 0 else -am/(-am+100)
def payout(am): return am/100 if am > 0 else 100/-am
def logit(p): p = np.clip(p, 1e-4, 1-1e-4); return np.log(p/(1-p))
def sig(x): return 1/(1+np.exp(-x))
def settle(x): return np.where(x > 0, 1.0, np.where(x < 0, -1.0, 0.0))

def roll(long, cols, key="team"):
    """pregame expanding mean of each col over the team's prior games (no leakage), z-scored."""
    long = long.sort_values("date"); out = []
    for t, d in long.groupby(key):
        m = d[cols].expanding().mean().shift(1); n = np.arange(len(d))
        m[n < 2] = np.nan; out.append(m.assign(**{key: t, "date": d.date.values, "gid": d.gid.values}))
    r = pd.concat(out)
    for c in cols: r[c] = (r[c]-r[c].mean())/r[c].std()
    return r.fillna(0.0)

def nfl():
    g = pd.read_csv(C+"nflverse_games.csv"); g = g[g.season >= 2025].copy(); g["date"] = pd.to_datetime(g.gameday)
    st = pd.concat([pd.read_csv(C+f"nfl_stats_team_week_{s}.csv") for s in (2025, 2026)])
    st["plays"] = st.attempts+st.carries+st.sacks_suffered
    st["epa"] = (st.passing_epa.fillna(0)+st.rushing_epa.fillna(0))/st.plays
    st["sackr"] = st.sacks_suffered/(st.attempts+st.sacks_suffered)
    st["press"] = st.def_sacks.fillna(0)+st.def_qb_hits.fillna(0)
    td = st.passing_tds+st.rushing_tds
    st["rz"] = td/(td+st.fg_att.fillna(0)).replace(0, np.nan)
    o = st[["game_id", "team", "epa", "sackr", "plays"]].rename(columns={"team": "opp", "epa": "oepa", "sackr": "osackr", "plays": "oplays"})
    st = st.merge(o, on="game_id"); st = st[st.team != st.opp]
    st["tci"] = (st.epa-st.oepa)*10 + (st.osackr-st.sackr)*10 + st.press*0.05
    st["ppe"] = (st.plays-st.oplays)*0.02 + st.rz.fillna(0.5)
    st = st.merge(g[["game_id", "date"]], on="game_id").rename(columns={"game_id": "gid"})
    R = roll(st, ["tci", "ppe"])
    inj = pd.read_csv(C+"nfl_injuries_2026.csv")
    sc = [c for c in inj.columns if "report_status" in c.lower()] or [c for c in inj.columns if "status" in c.lower()]
    q = inj[inj[sc[0]].isin(["Questionable", "Doubtful", "Out"])].groupby(["team", "week"]).size()
    thr = q.quantile(0.85)
    df = pd.DataFrame({"gid": g.game_id, "date": g.date, "home": g.home_team, "away": g.away_team,
                       "hs": g.home_score, "as_": g.away_score, "spread": -g.spread_line, "total": g.total_line,
                       "hml": g.home_moneyline, "aml": g.away_moneyline, "hsp": g.home_spread_odds, "asp": g.away_spread_odds,
                       "op": g.over_odds, "up": g.under_odds, "neutral": g.location.eq("Neutral"),
                       "rival": g.div_game.eq(1), "short_rest": (g.home_rest.fillna(7) <= 4) | (g.away_rest.fillna(7) <= 4),
                       "inj": [max(q.get((h, w), 0), q.get((a, w), 0)) >= thr if s == 2026 else False
                               for h, a, w, s in zip(g.home_team, g.away_team, g.week, g.season)]})
    return df, R, dict(sd_m=13.5, sd_t=13.0, hfa=1.5, blow=17)

def cfb():
    g = pd.concat([pd.read_parquet(C+f"cfb_games_{s}.parquet") for s in (2025, 2026)])
    sch = pd.concat([pd.read_parquet(C+f"cfb_sched_{s}.parquet") for s in (2025, 2026)])[["game_id", "conference_game"]]
    g["game_id"] = g.game_id.astype(str); sch["game_id"] = sch.game_id.astype(str)
    g = g.merge(sch, on="game_id", how="left"); g = g[g.home_fbs & g.away_fbs].copy(); g["date"] = pd.to_datetime(g.date)
    tg = pd.concat([pd.read_parquet(C+f"cfb_teamgames_{s}.parquet") for s in (2025, 2026)]).drop_duplicates(["game_id", "team"])
    tg["game_id"] = tg.game_id.astype(str)
    o = tg[["game_id", "team", "success_rate", "stuff_rate", "sack_rate", "plays"]].rename(columns=lambda c: c if c == "game_id" else "o_"+c)
    tg = tg.merge(o, on="game_id"); tg = tg[tg.team != tg.o_team]
    tg["tci"] = (tg.success_rate-tg.o_success_rate)*10 + (tg.o_stuff_rate-tg.stuff_rate)*5 + (tg.o_sack_rate-tg.sack_rate)*5
    tg["ppe"] = (tg.plays-tg.o_plays)*0.02 + tg.redzone_td_rate.fillna(0.6)
    tg = tg.merge(g[["game_id", "date"]], on="game_id").rename(columns={"game_id": "gid"})
    R = roll(tg, ["tci", "ppe"])
    df = pd.DataFrame({"gid": g.game_id, "date": g.date, "home": g.home, "away": g.away, "hs": g.home_points, "as_": g.away_points,
                       "spread": g.spread, "total": g.total, "hml": g.home_ml, "aml": g.away_ml, "neutral": g.neutral,
                       "rival": g.conference_game.fillna(False).astype(bool) & (g.spread.abs() <= 7),
                       "short_rest": False, "inj": False})
    return df, R, dict(sd_m=15.5, sd_t=16.0, hfa=2.5, blow=21)

def wnba():
    b = pd.concat([pd.read_parquet(C+f"wnba_box_{s}.parquet") for s in (2025, 2026)])
    for c in ("field_goals_attempted", "offensive_rebounds", "total_turnovers", "free_throws_attempted", "field_goals_made",
              "three_point_field_goals_made", "total_rebounds", "points_in_paint", "team_score", "opponent_team_score"):
        b[c] = pd.to_numeric(b[c], errors="coerce")
    b["date"] = pd.to_datetime(b.game_date); b["gid"] = b.game_id.astype(str)
    b["poss"] = b.field_goals_attempted - b.offensive_rebounds + b.total_turnovers + 0.44*b.free_throws_attempted
    b["efg"] = (b.field_goals_made + 0.5*b.three_point_field_goals_made)/b.field_goals_attempted
    o = b[["gid", "team_display_name", "total_rebounds", "points_in_paint", "efg"]].rename(columns=lambda c: c if c == "gid" else "o_"+c)
    b = b.merge(o, on="gid"); b = b[b.team_display_name != b.o_team_display_name]
    b["tci"] = (b.total_rebounds-b.o_total_rebounds)*0.1 + (b.points_in_paint-b.o_points_in_paint)*0.05
    b["ppe"] = (b.poss-b.poss.mean())*0.05 + (b.efg-b.o_efg)*10
    b = b.rename(columns={"team_display_name": "team"}); R = roll(b, ["tci", "ppe"])
    L = pd.read_csv(C+"wnba_lines_hist.csv", dtype={"game_id": str})
    h = b[b.team_home_away == "home"][["gid", "date", "team", "team_score", "o_team_display_name", "opponent_team_score", "season_type"]]
    h.columns = ["gid", "date", "home", "hs", "away", "as_", "stype"]
    g = h.merge(L, left_on="gid", right_on="game_id").sort_values("date")
    met = [((g.date < d) & (g.date >= d-pd.Timedelta(days=4)) & (((g.home == x) & (g.away == y)) | ((g.home == y) & (g.away == x)))).any()
           for d, x, y in zip(g.date, g.home, g.away)]
    df = pd.DataFrame({"gid": g.gid, "date": g.date, "home": g.home, "away": g.away, "hs": g.hs, "as_": g.as_, "spread": g.spread,
                       "total": g.total, "hml": g.home_ml, "aml": g.away_ml, "neutral": False,
                       "rival": np.array(met) & (g.stype.values == 3) & (g.spread.abs().values <= 4), "short_rest": False, "inj": False})
    return df, R, dict(sd_m=11.5, sd_t=15.0, hfa=2.0, blow=20)

def dec2am(d): d = np.asarray(d, float); return np.where(d >= 2, (d-1)*100, -100/(d-1))

def soccer():
    from marv.soccer_max.data import _normalize
    fr = []
    for f in sorted(glob.glob(C+"soccer_fd/*_2526.csv")+glob.glob(C+"soccer_fd/*_2627.csv")):
        raw = pd.read_csv(f, on_bad_lines="skip"); raw.columns = [str(c).replace("\ufeff", "").replace("ï»¿", "").strip() for c in raw.columns]
        d = _normalize(raw.copy(), f.split("/")[-1].split("_")[0], 0)
        for c in ("AHh", "AvgAHH", "AvgAHA"): d[c] = pd.to_numeric(raw.loc[d.index, c], errors="coerce").values if c in raw else np.nan
        fr.append(d)
    g = pd.concat(fr).dropna(subset=["o_h", "o_a", "o_d"]).reset_index(drop=True); g["gid"] = g.index.astype(str)
    lo = []
    for s, o, side in (("h", "a", "home"), ("a", "h", "away")):
        lo.append(pd.DataFrame({"gid": g.gid, "date": g.date, "team": g[side], "sot": g[s+"st"], "osot": g[o+"st"],
                                "sh": g["hs" if s == "h" else "as_"], "osh": g["as_" if s == "h" else "hs"], "gf": g[s+"g"]}))
    lo = pd.concat(lo).dropna(subset=["gf"])
    lo["tci"] = (lo.sot-lo.osot)*0.2; lo["ppe"] = (lo.sh+lo.osh-25)*0.05 + (lo.gf/lo.sot.replace(0, np.nan)).fillna(0.3)
    R = roll(lo, ["tci", "ppe"])
    df = pd.DataFrame({"gid": g.gid, "date": g.date, "home": g.home, "away": g.away, "hs": g.hg, "as_": g.ag, "spread": g.AHh,
                       "total": 2.5, "hml": dec2am(g.o_h), "aml": dec2am(g.o_a),
                       "op": dec2am(g.o_over), "up": dec2am(g.o_under), "hsp": dec2am(g.AvgAHH), "asp": dec2am(g.AvgAHA),
                       "neutral": False, "rival": False, "short_rest": False, "inj": False})
    return df, R, dict(soccer=True, hfa=0.25, blow=3)

def run(name, df, R, P):
    fin = df.dropna(subset=["hs", "as_"]).sort_values("date").reset_index(drop=True)
    Rk = R.set_index(["gid", "team"])[["tci", "ppe"]]; Rk = Rk[~Rk.index.duplicated()]
    bets = []
    test = fin[(fin.date >= T0) & (fin.date < T1)]
    for _, x in test.iterrows():
        past = fin[(fin.date < x.date) & (fin.date >= x.date-pd.Timedelta(days=400))]
        def rate(t):
            a = past[past.home == t]; b = past[past.away == t]; n = len(a)+len(b)
            if n < 3: return None
            return (a.hs.sum()+b.as_.sum())/n, (a.as_.sum()+b.hs.sum())/n
        rh, ra = rate(x.home), rate(x.away)
        if not rh or not ra: continue
        Lg = (past.hs.mean()+past.as_.mean())/2; hfa = 0 if x.neutral else P["hfa"]
        eh = rh[0]+ra[1]-Lg+hfa/2; ea = ra[0]+rh[1]-Lg-hfa/2
        if P.get("soccer"):
            eh, ea = max(0.2, 0.5*eh+0.5*Lg*1.1), max(0.2, 0.5*ea+0.5*Lg*0.9)
            hg = rng.poisson(eh, N); ag = rng.poisson(ea, N); m = hg-ag; t = hg+ag
        else:
            if pd.notna(x.spread) and pd.notna(x.total):  # 50/50 shrink toward market-implied score
                mh, ma = (x.total-x.spread)/2, (x.total+x.spread)/2; eh, ea = 0.5*eh+0.5*mh, 0.5*ea+0.5*ma
            m = rng.normal(eh-ea, P["sd_m"], N); t = rng.normal(eh+ea, P["sd_t"], N)
        def z(team):
            try: return Rk.loc[(x.gid, team)]
            except KeyError: return pd.Series({"tci": 0.0, "ppe": 0.0})
        zh, za = z(x.home), z(x.away)
        dT, dP, sP = float(zh.tci-za.tci), float(zh.ppe-za.ppe), float(zh.ppe+za.ppe)
        def trend(team):
            a = past[((past.home == team) | (past.away == team)) & past.spread.notna()].tail(TFN)
            if len(a) < 2: return 0.0, 0.0
            mr = np.where(a.home == team, (a.hs-a.as_)+a.spread, (a.as_-a.hs)-a.spread)
            tr = (a.hs+a.as_-a.total).values
            return float(np.nanmean(mr)), float(np.nanmean(tr))
        (mh_, th_), (ma_, ta_) = trend(x.home), trend(x.away)
        sdm = 1.6 if P.get("soccer") else P["sd_m"]; sdt = 1.6 if P.get("soccer") else P["sd_t"]
        tf_side = float(np.clip(TFMAX*np.tanh((mh_-ma_)/sdm), -TFMAX, TFMAX))
        tf_tot = float(np.clip(TFMAX*np.tanh((th_+ta_)/2/sdt), -TFMAX, TFMAX))
        def lastm(team):
            a = past[(past.home == team) | (past.away == team)].tail(1)
            if a.empty: return 0
            r = a.iloc[0]; return (r.hs-r.as_) if r.home == team else (r.as_-r.hs)
        emo = bool(min(lastm(x.home), lastm(x.away)) <= -P["blow"]) or bool(x.short_rest)
        uf = 0 if (x.rival or emo or x.inj) else 1
        why = ",".join(k for k, v in (("rivalry", x.rival), ("emotional", emo), ("injury", x.inj)) if v)
        hm, am_ = (m > 0).mean(), (m < 0).mean(); mk = []
        if pd.notna(x.hml) and pd.notna(x.aml):
            ph, pa = (hm, am_) if P.get("soccer") else (hm/(hm+am_), am_/(hm+am_))
            mk += [("ml", ph, x.hml, 1, 1 if x.hs > x.as_ else -1), ("ml", pa, x.aml, -1, 1 if x.as_ > x.hs else -1)]
        def px(v): return -110 if pd.isna(v) else v
        if pd.notna(x.spread):
            c = settle(m+x.spread); w, l = (c > 0).mean(), (c < 0).mean(); res = float(np.sign(x.hs-x.as_+x.spread))
            if w+l > 0: mk += [("spread", w/(w+l), px(x.get("hsp")), 1, res), ("spread", l/(w+l), px(x.get("asp")), -1, -res)]
        if pd.notna(x.total):
            c = settle(t-x.total); w, l = (c > 0).mean(), (c < 0).mean(); res = float(np.sign(x.hs+x.as_-x.total))
            if w+l > 0: mk += [("total", w/(w+l), px(x.get("op")), 2, res), ("total", l/(w+l), px(x.get("up")), -2, -res)]
        for mkt in ("ml", "spread", "total"):
            c = [r for r in mk if r[0] == mkt]
            for model in ("off", "on"):
                best = None
                for (_, p, price, sgn, res) in c:
                    p = sig(logit(p) + np.sign(sgn)*(K*sP if abs(sgn) == 2 else K*(dT+dP)))
                    if model == "on":
                        p = float(np.clip(p + np.sign(sgn)*(tf_tot if abs(sgn) == 2 else tf_side), 0.001, 0.999))
                    e = p-be(price)
                    if e >= EDGE and (best is None or e > best[0]): best = (e, sgn, price, res)
                if best and name == "soccer" and mkt == "ml": best = None
                if best and uf == 0 and name in ("cfb", "soccer"): best = None
                if best:
                    e, sgn, price, res = best
                    u = payout(price) if res > 0 else (-1.0 if res < 0 else 0.0)
                    bets.append(dict(sport=name, date=x.date, game=f"{x.away}@{x.home}", market=mkt, model=model, side=sgn,
                                     edge=round(e, 3), units=u, uf=uf, why=why, gid=x.gid, tf=round(tf_tot if mkt == "total" else tf_side, 4)))
    return pd.DataFrame(bets)

def summ(b):
    if b.empty: return dict(n=0)
    b = b.sort_values("date"); cu = b.units.cumsum(); dd = float((np.maximum.accumulate(np.r_[0, cu.values])[1:]-cu.values).max())
    s = ls = 0
    for u in b.units: ls = ls+1 if u < 0 else (ls if u == 0 else 0); s = max(s, ls)
    w, l = int((b.units > 0).sum()), int((b.units < 0).sum())
    return dict(n=len(b), W=w, L=l, P=len(b)-w-l, units=round(b.units.sum(), 2), roi=round(b.units.sum()/len(b), 3), maxdd=round(dd, 2), streak=s)

if __name__ == "__main__":
    allb = []
    for nm, fn in (("nfl", nfl), ("cfb", cfb), ("wnba", wnba), ("soccer", soccer)):
        try:
            df, R, P = fn(); b = run(nm, df, R, P); allb.append(b); print(nm, "bets", len(b), flush=True)
        except Exception as ex:
            import traceback; traceback.print_exc(); print(nm, "FAILED", ex)
    B = pd.concat(allb)
    # CLV (CFB only: open->close from cfb_lines_oc): points the line moved toward our side
    try:
        oc = pd.read_csv(C+"cfb_lines_oc.csv", dtype={"game_id": str}).drop_duplicates("game_id").set_index("game_id")
        def clv(r):
            if r.sport != "cfb" or str(r.gid) not in oc.index: return np.nan
            o = oc.loc[str(r.gid)]
            if r.market == "spread": return (o.spread_open - o.spread) * (1 if r.side == 1 else -1) if pd.notna(o.spread_open) else np.nan
            if r.market == "total": return (o.total - o.total_open) * (1 if r.side == 2 else -1) if pd.notna(o.total_open) else np.nan
            return np.nan
        B["clv"] = B.apply(clv, axis=1)
    except Exception as ex: print("clv", ex); B["clv"] = np.nan
    B.to_csv(f"bets_tf_{T0.date()}.csv", index=False); out = {}
    def sm(b):
        d = summ(b)
        if len(b) and b.clv.notna().any(): d["clv_pts"] = round(float(b.clv.mean()), 2)
        return d
    for sp in ["ALL"]+sorted(B.sport.unique()):
        S = B if sp == "ALL" else B[B.sport == sp]
        for mkt in ["all", "ml", "spread", "total"]:
            M = S if mkt == "all" else S[S.market == mkt]
            out[f"{sp}/{mkt}"] = dict(on=sm(M[M.model == "on"]), off=sm(M[M.model == "off"]))
    json.dump(out, open(f"summary_tf_{T0.date()}.json", "w"), indent=1, default=str)
    for k, v in out.items(): print(k, json.dumps(v))
