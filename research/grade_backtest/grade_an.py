import numpy as np, pandas as pd
G=pd.read_pickle("grade_games.pkl")
G["margin"]=G.home_points-G.away_points; G["tot"]=G.home_points+G.away_points
G["gap"]=G.gh-G.ga                       # + = home higher grade
G["fav_home"]=G.gap>=0
G["hi"]=np.maximum(G.gh,G.ga); G["lo"]=np.minimum(G.gh,G.ga); G["agap"]=G.hi-G.lo
G["hi_win"]=np.where(G.fav_home,G.margin>0,G.margin<0).astype(float)
# ATS: higher-grade team covers (spread = home line; home covers if margin+spread>0)
cov=np.sign(G.margin+G.spread)
G["hi_cov"]=np.where(G.fav_home,cov>0,cov<0).astype(float); G.loc[cov==0,"hi_cov"]=np.nan
G.loc[G.spread.isna(),"hi_cov"]=np.nan
# market favorite for comparison
G["mkt_home_fav"]=G.spread<0
G["mkt_fav_win"]=np.where(G.mkt_home_fav,G.margin>0,G.margin<0).astype(float)
ou=np.sign(G.tot-G.total); G["over"]=(ou>0).astype(float); G.loc[(ou==0)|G.total.isna(),"over"]=np.nan
# scoring grade: both offenses good + both defenses weak -> high
G["score_grade"]=(G.oh+G.oa+(100-G.dh)+(100-G.da))/4
print("n",len(G)," overall: higher-grade wins %.3f | market favorite wins %.3f (same games)"%(G.hi_win.mean(),G.mkt_fav_win.mean()))
print("\nGAP BUCKETS (higher-grade team): ML win, ATS cover, n")
b=pd.cut(G.agap,[0,5,10,15,20,30,40,60,100],include_lowest=True)
t=G.groupby(b).agg(n=("hi_win","size"),ml=("hi_win","mean"),ats=("hi_cov","mean"),natS=("hi_cov","count"),mkt=("mkt_fav_win","mean"))
print(t.round(3).to_string())
print("\nSCORING GRADE quintiles: Over rate")
q=pd.qcut(G.score_grade,5)
print(G.groupby(q).agg(n=("over","count"),over=("over","mean")).round(3).to_string())
# specific matchups like 90 vs 75
print("\nCells (higher grade band x lower grade band): n, ML, ATS, Over")
G["hb"]=pd.cut(G.hi,[0,60,75,90,100.1],labels=["<60","60-75","75-90","90+"]); G["lb"]=pd.cut(G.lo,[-1,25,50,75,100],labels=["<25","25-50","50-75","75+"])
c=G.groupby(["hb","lb"],observed=True).agg(n=("hi_win","size"),ml=("hi_win","mean"),ats=("hi_cov","mean"),over=("over","mean"))
print(c[c.n>=30].round(3).to_string())
