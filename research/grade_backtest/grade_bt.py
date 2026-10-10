import numpy as np, pandas as pd, warnings, sys; warnings.filterwarnings("ignore")
from sklearn.linear_model import Ridge
C="state/cache/"; SEAS=list(range(2015,2026))
games=pd.concat([pd.read_parquet(C+f"cfb_games_{s}.parquet").assign(season=s) for s in SEAS],ignore_index=True)
games["date"]=pd.to_datetime(games["date"]); games=games.sort_values("date")
tgs=pd.concat([pd.read_parquet(C+f"cfb_teamgames_{s}.parquet") for s in SEAS],ignore_index=True)
stats=[c for c in tgs.columns if c not in("game_id","team","plays","drives","rush_att","dropbacks","yards","rush_yds","pass_yds","scoring_opps","penalty_yards_for")]
tgs=tgs.drop_duplicates(["game_id","team"])
long=[]
for side,opp in (("home","away"),("away","home")):
    p=games[["game_id","season","date",side,opp,f"{side}_points",f"{opp}_points"]].copy(); p.columns=["game_id","season","date","team","opp","pts","opp_pts"]; p["home"]=1.0 if side=="home" else 0.0; long.append(p)
L=pd.concat(long,ignore_index=True)
L=L.merge(tgs[["game_id","team"]+stats],on=["game_id","team"],how="left")
opp_stats=tgs[["game_id","team"]+stats].rename(columns={"team":"opp",**{c:"d_"+c for c in stats}})
L=L.merge(opp_stats,on=["game_id","opp"],how="left").dropna(subset=["pts","ypp"]).sort_values(["team","date"]).reset_index(drop=True)
# pregame season-to-date means with prior-season carryover (k=4): offense = own stats, defense = opponent's stats vs this team
K=4
O=stats; D=["d_"+c for c in stats]
rows=[]
for (team,season),d in L.groupby(["team","season"]):
    d=d.sort_values("date")
    prev=L[(L.team==team)&(L.season==season-1)]
    pm=prev[O+D].mean() if len(prev) else None
    cum=d[O+D].cumsum().shift(1).fillna(0); n=np.arange(len(d)).astype(float)
    base=(pm if pm is not None else d[O+D].mean()*0)  # no prior season: shrink toward 0 later via league mean fill
    est=(cum.values+K*(base.values if pm is not None else np.nan))/ (n[:,None]+K) if pm is not None else cum.values/np.maximum(n[:,None],1)
    e=pd.DataFrame(est,index=d.index,columns=["m_"+c for c in O+D]); e["n"]=n
    rows.append(pd.concat([d,e],axis=1))
F=pd.concat(rows).sort_values("date").reset_index(drop=True)
mcols=["m_"+c for c in O+D]
F[mcols]=F[mcols].astype(float)
# z-score against league that season (all teams' pregame means at all dates: mean/sd of columns by season)
for c in mcols:
    g=F.groupby("season")[c]; F["z_"+c]=(F[c]-g.transform("mean"))/g.transform("std")
zo=["z_m_"+c for c in O]; zd=["z_m_"+c for c in D]
F[zo+zd]=F[zo+zd].fillna(0.0)
# team-game training rows: points ~ own offense z + opponent defense z (+home)
opp=F[["game_id","team"]+zd+zo].rename(columns={"team":"opp",**{c:"opp_"+c for c in zd+zo}})
T=F.merge(opp,on=["game_id","opp"],how="inner")
Xcols=zo+["opp_"+c for c in zd]+["home"]
# the opponent's defensive columns: opponent's "d_" means = what opponents did against them
# walk-forward ridge per test season
power=[]
for s in range(2018,2026):
    tr=T[(T.season<s)&(T.season>=s-6)]; te=F[F.season==s]
    m=Ridge(alpha=300).fit(tr[Xcols],tr.pts)
    # offense power: own offense z, average-defense opponent; defense power: opponent average offense, own defense (their allowed stats)
    off_X=te[zo].copy(); 
    for c in ["opp_"+c for c in zd]: off_X[c]=0.0
    off_X["home"]=0.5; off_X=off_X[Xcols]
    off=m.predict(off_X)
    # defense: swap roles -> opponent offense average (0), opponent defense cols = this team's *allowed-stat* z (zd)
    def_X=pd.DataFrame(0.0,index=te.index,columns=Xcols)
    for c in zd: def_X["opp_"+c]=te[c].values
    def_X["home"]=0.5
    de=m.predict(def_X)   # points an average offense scores vs this defense (lower = better defense)
    power.append(pd.DataFrame({"game_id":te.game_id,"team":te.team,"season":s,"off":off,"deff":-de,"n":te.n}))
P=pd.concat(power); P["power"]=P.off+P.deff
# percentile grades within season among teams at that date: use rank within the season of pregame values (cross-section by game week)
P["wk"]=P.game_id.map(games.set_index("game_id").date)
P["grade"]=P.groupby(["season"])["power"].rank(pct=True)*100
P["og"]=P.groupby("season")["off"].rank(pct=True)*100
P["dg"]=P.groupby("season")["deff"].rank(pct=True)*100
G=games[games.home_fbs & games.away_fbs][["game_id","season","date","home","away","home_points","away_points","spread","total","home_ml","away_ml"]]
G=G.merge(P.rename(columns={"team":"home","grade":"gh","og":"oh","dg":"dh","power":"ph","n":"nh"})[["game_id","home","gh","oh","dh","ph","nh"]],on=["game_id","home"])
G=G.merge(P.rename(columns={"team":"away","grade":"ga","og":"oa","dg":"da","power":"pa","n":"na"})[["game_id","away","ga","oa","da","pa","na"]],on=["game_id","away"])
G=G[(G.nh>=3)&(G.na>=3)].copy()
G.to_pickle("grade_games.pkl"); print(len(G),"FBS-vs-FBS games with grades; seasons",G.season.min(),G.season.max())
