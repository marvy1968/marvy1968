"""UFP/UVF backtest (paper research). Pregame-only features (shift(1) rolling). NFL: ML ROI. CFB: no cached ML -> ATS at -110 + outright vs spread-implied."""
import numpy as np, pandas as pd, json, sys, warnings; warnings.filterwarnings("ignore")
from scipy.stats import norm
C="/opt/marv-bot/state/cache/"
def imp(a): return np.where(a>0,100/(a+100),-a/(-a+100))
def pay(a): return np.where(a>0,a/100,100/-a)

def nfl_team():
    st=pd.concat([pd.read_csv(C+f"nfl_stats_team_week_{s}.csv") for s in range(2020,2027)])
    st=st[st.season_type.eq("REG")|st.season_type.isna()] if "season_type" in st else st
    st["plays"]=st.attempts+st.carries+st.sacks_suffered
    st["yds"]=st.passing_yards+st.rushing_yards
    st["epa"]=(st.passing_epa.fillna(0)+st.rushing_epa.fillna(0))/st.plays
    td=st.passing_tds+st.rushing_tds; st["rz"]=td/(td+st.fg_att.fillna(0)).replace(0,np.nan)
    st["give"]=st.passing_interceptions+st.sack_fumbles_lost+st.rushing_fumbles_lost+st.receiving_fumbles_lost
    st["fum"]=st.fumbles_total.fillna(0); st["fumrec"]=st.fumble_recovery_own.fillna(0)
    st["press"]=st.def_sacks.fillna(0)+st.def_qb_hits.fillna(0)
    o=st[["game_id","team","epa","yds","plays","give","fum","fumrec","press","sacks_suffered","attempts"]].rename(columns=lambda c:c if c=="game_id" else "o_"+c)
    st=st.merge(o,on="game_id"); st=st[st.team!=st.o_team]
    st["ypp"]=st.yds/st.plays; st["netepa"]=st.epa-st.o_epa; st["tom"]=st.o_give-st.give
    st["dsackr"]=st.def_sacks.fillna(0)/(st.o_attempts+st.o_sacks_suffered)
    # fumble-recovery luck: share of ALL fumbles in game recovered by this team (expected ~0.5)
    st["allfum"]=st.fum+st.o_fum; st["recov"]=st.fumrec+(st.o_fum-st.o_fumrec)
    return st

def roll_feats(st,date_map):
    st=st.merge(date_map,on="game_id").sort_values("date"); out=[]
    for (t,s),d in st.groupby(["team","season"]):
        sh=lambda c:d[c].expanding().mean().shift(1)
        f=pd.DataFrame({"game_id":d.game_id,"team":t,"n":np.arange(len(d)),
          "epa_s":sh("netepa"),"ypp_s":sh("ypp"),"rz_s":sh("rz"),"tom_s":sh("tom"),"press_s":sh("press"),"dsack_s":sh("dsackr"),
          "pace3":d.plays.rolling(3).mean().shift(1),"var3":d.netepa.rolling(3).std().shift(1),
          "rz3":d.rz.rolling(3,min_periods=2).mean().shift(1),
          "fumluck":(d.recov.cumsum().shift(1)+1)/(d.allfum.cumsum().shift(1)+2),
          "lastmarg":(d.get("marg",pd.Series(np.nan,index=d.index))).shift(1)})
        out.append(f)
    return pd.concat(out)

def nfl():
    g=pd.read_csv(C+"nflverse_games.csv"); g=g[(g.season>=2020)&(g.game_type=="REG")&g.home_moneyline.notna()&g.home_score.notna()].copy()
    g["date"]=pd.to_datetime(g.gameday)
    st=nfl_team()
    sc=pd.concat([g[["game_id","home_team","home_score","away_score"]].rename(columns={"home_team":"team","home_score":"pf","away_score":"pa"}),
                  g[["game_id","away_team","away_score","home_score"]].rename(columns={"away_team":"team","away_score":"pf","home_score":"pa"})])
    st=st.merge(sc,on=["game_id","team"],how="left"); st["marg"]=st.pf-st.pa; st["win"]=(st.marg>0).astype(float)
    F=roll_feats(st,g[["game_id","date"]])
    W=st.merge(g[["game_id","date"]],on="game_id").sort_values("date")
    W["wp_s"]=W.groupby(["team","season"]).win.transform(lambda x:x.expanding().mean().shift(1))
    F=F.merge(W[["game_id","team","wp_s"]],on=["game_id","team"])
    # schedule: next game opponent & whether div / strong
    allg=pd.read_csv(C+"nflverse_games.csv"); allg=allg[allg.season>=2020]
    sl=pd.concat([allg[["season","week","home_team","away_team","div_game"]].rename(columns={"home_team":"team","away_team":"opp"}),
                  allg[["season","week","away_team","home_team","div_game"]].rename(columns={"away_team":"team","home_team":"opp"})]).sort_values(["team","season","week"])
    sl["nxt_opp"]=sl.groupby(["team","season"]).opp.shift(-1); sl["nxt_div"]=sl.groupby(["team","season"]).div_game.shift(-1)
    rows=[]
    for _,r in g.iterrows():
        for side,opp,ml,oml,pf,pa in (("home",r.away_team,r.home_moneyline,r.away_moneyline,r.home_score,r.away_score),
                                      ("away",r.home_team,r.away_moneyline,r.home_moneyline,r.away_score,r.home_score)):
            rows.append(dict(sport="nfl",gid=r.game_id,season=r.season,week=r.week,date=r.date,team=r[side+"_team"],opp=opp,ml=ml,oml=oml,won=float(pf>pa),push=pf==pa,div=r.div_game))
    D=pd.DataFrame(rows)
    D=D.merge(F.add_prefix("t_").rename(columns={"t_game_id":"gid","t_team":"team"}),on=["gid","team"],how="left")
    D=D.merge(F.add_prefix("o_").rename(columns={"o_game_id":"gid","o_team":"opp"}),on=["gid","opp"],how="left")
    D=D.merge(sl[["team","season","week","nxt_opp","nxt_div"]],on=["team","season","week"],how="left")
    # next opponent strength = its win pct entering this week
    wp=D[["team","season","week","t_wp_s"]].rename(columns={"team":"nxt_opp","t_wp_s":"nxt_wp"})
    D=D.merge(wp,on=["nxt_opp","season","week"],how="left")
    p=imp(D.ml);q=imp(D.oml);D["impl"]=p/(p+q);D["rawimp"]=p
    return D

def tags_nfl(D):
    ok=D.t_n>=3
    # season ranks within season-week cross-section (percentile among all teams)
    def pct(c): return D.groupby(["season","week"])[c].rank(pct=True)
    D["epa_p"]=pct("t_epa_s");D["pace_p"]=pct("t_pace3");D["var_p"]=pct("t_var3");D["ypp_p"]=pct("t_ypp_s");D["press_p"]=pct("t_press_s")
    D["o_epa_p"]=pct("o_epa_s");D["o_dsack_p"]=pct("o_dsack_s");D["t_dsack_p"]=pct("t_dsack_s")
    D["ufp_pace"]=ok&(D.epa_p>=0.6)&((D.pace_p<=0.25)|(D.var_p>=0.75))
    D["ufp_look"]=ok&(D.o_wp_s<=0.4)&((D.nxt_div==1)|(D.nxt_wp>=0.6))
    D["ufp_rz"]=ok&(D.ypp_p>=0.6)&(D.t_rz3<=D.t_rz_s-0.10)
    D["ufp_to"]=ok&(D.t_tom_s>0)&(D.t_fumluck>=0.6)&(D.press_p<=0.5)
    D["ufp_any"]=D[["ufp_pace","ufp_look","ufp_rz","ufp_to"]].any(axis=1)
    # UVF on the dog (row team = dog)
    D["uvf_eff"]=ok&(D.o_n>=3)&(D.t_epa_s>=D.o_epa_s-0.02)       # pace-independent per-play efficiency ~ fav's
    D["uvf_overreact"]=ok&((D.t_lastmarg<=-17)|(D.o_lastmarg>=21))
    D["uvf_trench"]=ok&(D.t_dsack_p>=0.66)&(D.o_dsack_p<=0.5)
    D["uvf_any"]=D[["uvf_eff","uvf_overreact","uvf_trench"]].any(axis=1)
    return D

def summarize(sub,mode):
    n=len(sub)
    if n==0: return dict(n=0)
    win=sub.won.mean(); impl=sub.impl.mean(); roi=(np.where(sub.won==1,pay(sub.ml),-1)*(~sub.push)).mean()
    fade=(np.where(sub.won==0,pay(sub.oml),-1)*(~sub.push)).mean()
    return dict(n=n,win=round(win,3),impl=round(impl,3),diff=round(win-impl,3),roi_side=round(roi,3),roi_fade=round(fade,3))

D=tags_nfl(nfl()); D=D[~D.push]
W={"aug1_oct12_2026":(D.date>="2026-08-01")&(D.date<="2026-10-12"),"sep21_oct12_2026":(D.date>="2026-09-21")&(D.date<="2026-10-12"),
   "prior_2020_2025":D.season<=2025,"all":D.season>=2020}
res={}
for thr_name,fav in (("fav<=-200",D.ml<=-200),("fav<=-150",D.ml<=-150)):
    for sig in ["base","ufp_pace","ufp_look","ufp_rz","ufp_to","ufp_any"]:
        m=fav&(True if sig=="base" else D[sig])
        res[f"UFP {thr_name} {sig}"]={w:summarize(D[m&wm],"fav") for w,wm in W.items()}
for thr_name,dog in (("dog>=+150",D.ml>=150),("dog>=+120",D.ml>=120)):
    for sig in ["base","uvf_eff","uvf_overreact","uvf_trench","uvf_any"]:
        m=dog&(True if sig=="base" else D[sig])
        res[f"UVF {thr_name} {sig}"]={w:summarize(D[m&wm],"dog") for w,wm in W.items()}
json.dump(res,open("/opt/marv-bot/experiments/upset/summary_nfl.json","w"),indent=1,default=float)
for k,v in res.items():
    print(k, " | ".join(f"{w}: n={s.get('n')} w={s.get('win')} imp={s.get('impl')} roi={s.get('roi_side')} fade={s.get('roi_fade')}" for w,s in v.items()))
