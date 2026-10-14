"""CFB UFP/UVF backtest. No cached CFB moneylines -> evaluate vs closing spread at -110 (ATS) and outright win vs spread-implied P (normal sd 15.5)."""
import numpy as np, pandas as pd, json, warnings; warnings.filterwarnings("ignore")
from scipy.stats import norm
C="/opt/marv-bot/state/cache/"
g=pd.concat([pd.read_parquet(C+f"cfb_games_{s}.parquet") for s in range(2018,2027)]); g["game_id"]=g.game_id.astype(str)
sch=pd.concat([pd.read_parquet(C+f"cfb_sched_{s}.parquet") for s in range(2018,2027)])[["game_id","conference_game"]]; sch["game_id"]=sch.game_id.astype(str)
g=g.merge(sch.drop_duplicates("game_id"),on="game_id",how="left"); g=g[g.home_fbs&g.away_fbs&g.home_points.notna()].copy(); g["date"]=pd.to_datetime(g.date).dt.tz_localize(None)
tg=pd.concat([pd.read_parquet(C+f"cfb_teamgames_{s}.parquet") for s in range(2018,2027)]).drop_duplicates(["game_id","team"]); tg["game_id"]=tg.game_id.astype(str)
o=tg[["game_id","team","turnovers","sack_rate","stuff_rate","success_rate"]].rename(columns=lambda c:c if c=="game_id" else "o_"+c)
tg=tg.merge(o,on="game_id"); tg=tg[tg.team!=tg.o_team]
tg["tom"]=tg.o_turnovers-tg.turnovers; tg["net_sr"]=tg.success_rate-tg.o_success_rate; tg["dpress"]=tg.o_sack_rate+tg.o_stuff_rate
sc=pd.concat([g[["game_id","home","home_points","away_points","season","date"]].rename(columns={"home":"team","home_points":"pf","away_points":"pa"}),
              g[["game_id","away","away_points","home_points","season","date"]].rename(columns={"away":"team","away_points":"pf","home_points":"pa"})])
tg=tg.merge(sc,on=["game_id","team"]).sort_values("date"); tg["marg"]=tg.pf-tg.pa; tg["win"]=(tg.marg>0)*1.
F=[]
for (t,s),d in tg.groupby(["team","season"]):
    sh=lambda c:d[c].expanding().mean().shift(1)
    F.append(pd.DataFrame({"game_id":d.game_id,"team":t,"n":np.arange(len(d)),"sr_s":sh("net_sr"),"ypp_s":sh("ypp"),"rz_s":sh("redzone_td_rate"),
      "tom_s":sh("tom"),"dpress_s":sh("dpress"),"wp_s":sh("win"),"pace3":d.plays.rolling(3).mean().shift(1),"var3":d.net_sr.rolling(3).std().shift(1),
      "rz3":d.redzone_td_rate.rolling(3,min_periods=2).mean().shift(1),"lastmarg":d.marg.shift(1),
      "nxt_opp_conf":np.nan}))
F=pd.concat(F)
# schedule (all games incl. future) for lookahead
sl=pd.concat([g[["game_id","season","date","home","away","conference_game"]].rename(columns={"home":"team","away":"opp"}),
              g[["game_id","season","date","away","home","conference_game"]].rename(columns={"away":"team","home":"opp"})]).sort_values(["team","date"])
sl["nxt_opp"]=sl.groupby(["team","season"]).opp.shift(-1); sl["nxt_conf"]=sl.groupby(["team","season"]).conference_game.shift(-1)
rows=[]
for side,opp,sgn in (("home","away",1),("away","home",-1)):
    x=g[["game_id","season","date","spread",side,opp,"home_points","away_points"]].rename(columns={side:"team",opp:"opp"})
    x["line"]=x.spread*sgn; x["marg"]=(x.home_points-x.away_points)*sgn; rows.append(x)
D=pd.concat(rows); D=D[D.line.notna()]
D=D.merge(F.add_prefix("t_").rename(columns={"t_game_id":"game_id","t_team":"team"}),on=["game_id","team"],how="left")
D=D.merge(F.add_prefix("o_").rename(columns={"o_game_id":"game_id","o_team":"opp"}),on=["game_id","opp"],how="left")
D=D.merge(sl[["game_id","team","nxt_opp","nxt_conf"]],on=["game_id","team"],how="left")
D=D.merge(F[["game_id","team","wp_s"]].rename(columns={"team":"nxt_opp","wp_s":"nxt_wp_tmp"}),on=["game_id","nxt_opp"],how="left")  # unused (nxt game id differs)
# next-opponent strength: its season win pct at its latest game before this date
lw=tg[["team","date","win","season"]].copy()
lw["wp_cum"]=lw.groupby(["team","season"]).win.transform(lambda x:x.expanding().mean())
lw=lw.sort_values("date")
D=D.sort_values("date")
D=pd.merge_asof(D,lw[["date","team","wp_cum"]].rename(columns={"team":"nxt_opp","wp_cum":"nxt_wp"}),on="date",by="nxt_opp",allow_exact_matches=False)
D["cover"]=np.sign(D.marg+D.line); D["won"]=(D.marg>0)*1.; D["pimp"]=norm.cdf(-D.line/15.5)
ok=(D.t_n>=3)&(D.o_n>=3)
def pct(c): return D.groupby(["season",D.date.dt.isocalendar().week])[c].rank(pct=True)
for c in ("t_sr_s","t_pace3","t_var3","t_ypp_s","t_dpress_s","o_dpress_s"): D[c+"_p"]=pct(c)
D["ufp_pace"]=ok&(D.t_sr_s_p>=0.6)&((D.t_pace3_p<=0.25)|(D.t_var3_p>=0.75))
D["ufp_look"]=ok&(D.o_wp_s<=0.4)&(D.nxt_conf.fillna(False).astype(bool))&(D.nxt_wp>=0.6)
D["ufp_rz"]=ok&(D.t_ypp_s_p>=0.6)&(D.t_rz3<=D.t_rz_s-0.10)
D["ufp_to"]=ok&(D.t_tom_s>=0.75)&(D.t_dpress_s_p<=0.5)
D["ufp_any"]=D[["ufp_pace","ufp_look","ufp_rz","ufp_to"]].any(axis=1)
D["uvf_eff"]=ok&(D.t_sr_s>=D.o_sr_s-0.01)
D["uvf_overreact"]=ok&((D.t_lastmarg<=-21)|(D.o_lastmarg>=28))
D["uvf_trench"]=ok&(D.t_dpress_s_p>=0.66)&(D.o_dpress_s_p<=0.5)
D["uvf_any"]=D[["uvf_eff","uvf_overreact","uvf_trench"]].any(axis=1)
D=D[D.cover!=0]
def S(x):
    if not len(x): return dict(n=0)
    c=(x.cover>0).mean(); return dict(n=len(x),ats=round(c,3),ats_roi=round(c*1.909-1,3),win=round(x.won.mean(),3),pimp=round(x.pimp.mean(),3))
W={"aug1_oct12_2026":(D.date>="2026-08-01")&(D.date<="2026-10-12"),"sep21_oct12_2026":(D.date>="2026-09-21")&(D.date<="2026-10-12"),"prior_2018_2025":D.season<=2025}
res={}
for nm,m0 in (("fav<=-7",D.line<=-7),("fav<=-14",D.line<=-14)):
    for s in ["base","ufp_pace","ufp_look","ufp_rz","ufp_to","ufp_any"]:
        res[f"UFP {nm} {s}"]={w:S(D[m0&(True if s=='base' else D[s])&wm]) for w,wm in W.items()}
for nm,m0 in (("dog>=+7",D.line>=7),("dog>=+3.5",D.line>=3.5)):
    for s in ["base","uvf_eff","uvf_overreact","uvf_trench","uvf_any"]:
        res[f"UVF {nm} {s}"]={w:S(D[m0&(True if s=='base' else D[s])&wm]) for w,wm in W.items()}
json.dump(res,open("/opt/marv-bot/experiments/upset/summary_cfb.json","w"),indent=1,default=float)
for k,v in res.items(): print(k," | ".join(f"{w}: n={s.get('n')} ats={s.get('ats')} roi={s.get('ats_roi')} w={s.get('win')} p={s.get('pimp')}" for w,s in v.items()))
