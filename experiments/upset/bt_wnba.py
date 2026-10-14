"""WNBA UFP/UVF backtest: ML ROI using wnba_lines_hist.csv (consensus ML) + wnba_box. Clutch ORtg / altitude not in cache -> not tested."""
import numpy as np, pandas as pd, json, warnings; warnings.filterwarnings("ignore")
C="/opt/marv-bot/state/cache/"
def imp(a): return np.where(a>0,100/(a+100),-a/(-a+100))
def pay(a): return np.where(a>0,a/100,100/-a)
b=pd.concat([pd.read_parquet(C+f"wnba_box_{s}.parquet") for s in range(2018,2027)])
b=b[b.season_type.astype(str).isin(["2","regular"])|True]
for c in ("points_in_paint","defensive_rebounds","offensive_rebounds","blocks","team_score","opponent_team_score","field_goals_attempted","free_throws_attempted","total_turnovers"): b[c]=pd.to_numeric(b[c],errors="coerce")
b["gid"]=b.game_id.astype(str); b["date"]=pd.to_datetime(b.game_date); b["home"]=b.team_home_away.eq("home")
o=b[["gid","team_abbreviation","points_in_paint","defensive_rebounds","offensive_rebounds","blocks"]].rename(columns=lambda c:c if c=="gid" else "o_"+c)
b=b.merge(o,on="gid"); b=b[b.team_abbreviation!=b.o_team_abbreviation].sort_values("date")
b["poss"]=b.field_goals_attempted+0.44*b.free_throws_attempted+b.total_turnovers-b.offensive_rebounds
b["paintd"]=b.points_in_paint-b.o_points_in_paint; b["drebd"]=b.defensive_rebounds-b.o_defensive_rebounds; b["blk"]=b.blocks
b["netr"]=(b.team_score-b.opponent_team_score)/b.poss*100; b["marg"]=b.team_score-b.opponent_team_score
F=[]
for (t,s),d in b.groupby(["team_abbreviation","season"]):
    sh=lambda c:d[c].expanding().mean().shift(1)
    rest=d.date.diff().dt.days
    g3=d.date.rolling("4D",on="date").count() if False else pd.Series([((d.date>=x-pd.Timedelta(days=3))&(d.date<=x)).sum() for x in d.date],index=d.index)
    F.append(pd.DataFrame({"gid":d.gid,"team":t,"n":np.arange(len(d)),"paint_s":sh("paintd"),"dreb_s":sh("drebd"),"blk_s":sh("blk"),"net_s":sh("netr"),
        "b2b":rest.eq(1),"3in4":g3>=3,"lastmarg":d.marg.shift(1),"home":d.home}))
F=pd.concat(F)
L=pd.read_csv(C+"wnba_lines_hist.csv",dtype={"game_id":str}).rename(columns={"game_id":"gid"})
H=F[F.home].merge(F[~F.home],on="gid",suffixes=("_h","_a")).merge(L,on="gid").merge(b[b.home][["gid","date","marg"]],on="gid")
rows=[]
for s,o_ in (("h","a"),("a","h")):
    x=pd.DataFrame({"gid":H.gid,"date":H.date,"season":H.season,"ml":H[("home" if s=="h" else "away")+"_ml"],"oml":H[("away" if s=="h" else "home")+"_ml"],
      "won":(H.marg>0) if s=="h" else (H.marg<0)})
    for c in ("n","paint_s","dreb_s","blk_s","net_s","b2b","3in4","lastmarg"): x["t_"+c]=H[c+"_"+s].values; x["o_"+c]=H[c+"_"+o_].values
    rows.append(x)
D=pd.concat(rows).dropna(subset=["ml","oml"]); D["won"]=D.won.astype(float)
p=imp(D.ml);q=imp(D.oml);D["impl"]=p/(p+q); ok=(D.t_n>=5)&(D.o_n>=5)
D["ufp_sched"]=ok&(D.t_b2b|D["t_3in4"])&~(D.o_b2b|D["o_3in4"])
D["ufp_trench"]=ok&(D.t_paint_s+D.t_dreb_s<D.o_paint_s+D.o_dreb_s)
D["uvf_trench"]=ok&(D.t_paint_s>D.o_paint_s)&(D.t_dreb_s>D.o_dreb_s)&(D.t_blk_s>=D.o_blk_s)
D["uvf_sched"]=ok&(D.o_b2b|D["o_3in4"])&~(D.t_b2b|D["t_3in4"])
D["uvf_overreact"]=ok&((D.t_lastmarg<=-20)|(D.o_lastmarg>=20))
def S(x):
    if not len(x): return dict(n=0)
    return dict(n=len(x),win=round(x.won.mean(),3),impl=round(x.impl.mean(),3),roi_side=round(np.where(x.won==1,pay(x.ml),-1).mean(),3),roi_fade=round(np.where(x.won==0,pay(x.oml),-1).mean(),3))
W={"aug1_oct12_2026":(D.date>="2026-08-01")&(D.date<="2026-10-12"),"sep21_oct12_2026":(D.date>="2026-09-21")&(D.date<="2026-10-12"),"prior_2018_2025":D.season<=2025}
res={}
for s in ["base","ufp_sched","ufp_trench"]: res[f"UFP fav<=-200 {s}"]={w:S(D[(D.ml<=-200)&(True if s=='base' else D[s])&wm]) for w,wm in W.items()}
for s in ["base","uvf_trench","uvf_sched","uvf_overreact"]: res[f"UVF dog>=+150 {s}"]={w:S(D[(D.ml>=150)&(True if s=='base' else D[s])&wm]) for w,wm in W.items()}
print("max line date",D.date.max())
json.dump(res,open("/opt/marv-bot/experiments/upset/summary_wnba.json","w"),indent=1,default=float)
for k,v in res.items(): print(k," | ".join(f"{w}: n={s.get('n')} w={s.get('win')} imp={s.get('impl')} roi={s.get('roi_side')} fade={s.get('roi_fade')}" for w,s in v.items()))
