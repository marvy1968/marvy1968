import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("grade_bt26.py").read().split("# z-score against league")[0])   # builds F (pregame season-to-date means) and L
SC=70.0   # EPA/play -> points per game
F=F.sort_values(["team","date"]).reset_index(drop=True)
# rolling last-3 (prior games only) of own offense EPA and EPA allowed
F["r3_off"]=F.groupby("team")["epa_play"].transform(lambda s:s.shift(1).rolling(3,min_periods=3).mean())
F["r3_def"]=F.groupby("team")["d_epa_play"].transform(lambda s:s.shift(1).rolling(3,min_periods=3).mean())
F["base_off"]=F["m_epa_play"]*SC; F["base_def"]=F["m_d_epa_play"]*SC
F["off_delta"]=(F["r3_off"]*SC-F["base_off"]); F["def_delta"]=(F["r3_def"]*SC-F["base_def"])
F=F[(F.n>=3)].dropna(subset=["base_off","base_def","off_delta","def_delta"])
def hpr(d,aoff,adef,decay):
    dw=np.minimum(1.0,1.0/(1.0+np.abs(d.off_delta))) if decay else 1.0
    net=d.base_off-d.base_def
    return net+aoff*d.off_delta*dw - adef*d.def_delta*(1)   # def delta: allowing MORE than baseline hurts
H=games[games.home_fbs&games.away_fbs][["game_id","season","date","home","away","home_points","away_points","spread","total"]].dropna(subset=["home_points","spread"])
hf=F[["game_id","team","base_off","base_def","off_delta","def_delta"]]
H=H.merge(hf.rename(columns={"team":"home",**{c:"h_"+c for c in hf.columns if c not in("game_id","team")}}),on=["game_id","home"]).merge(hf.rename(columns={"team":"away",**{c:"a_"+c for c in hf.columns if c not in("game_id","team")}}),on=["game_id","away"])
H["margin"]=H.home_points-H.away_points; H["line"]=-H.spread
print("games",len(H),H.season.min(),H.season.max())
def diff(aoff,adef,decay):
    h=pd.DataFrame({"base_off":H.h_base_off,"base_def":H.h_base_def,"off_delta":H.h_off_delta,"def_delta":H.h_def_delta})
    a=pd.DataFrame({"base_off":H.a_base_off,"base_def":H.a_base_def,"off_delta":H.a_off_delta,"def_delta":H.a_def_delta})
    return (hpr(h,aoff,adef,decay)-hpr(a,aoff,adef,decay)).values
train=H.season<=2022; test=H.season>=2023
def evaluate(name,aoff,adef,decay):
    x=diff(aoff,adef,decay)
    A=np.c_[np.ones(len(H)),x,np.ones(len(H))]  # intercept = home field
    A=np.c_[np.ones(len(H)),x]
    beta=np.linalg.lstsq(A[train],H.margin[train],rcond=None)[0]
    pred=A@beta; res={}
    for nm,m in (("train",train),("TEST 2023-26",test),("2026",H.season==2026)):
        mae=np.mean(np.abs(pred[m]-H.margin[m])); mk=np.mean(np.abs(H.line[m]-H.margin[m]))
        edge=pred[m]-H.line[m]; ats=np.sign(H.margin[m]+H.spread[m]); out=f"{nm}: MAE {mae:.2f} (mkt {mk:.2f})"
        for th in (2,4):
            k=(np.abs(edge)>=th)&(ats!=0); hit=(np.sign(edge[k])==ats[k]).mean() if k.sum() else np.nan
            out+=f" | edge>={th}: ATS {hit:.3f} n={int(k.sum())}"
        res[nm]=out
    print(f"{name:42s} corr(test) {np.corrcoef(pred[test],H.margin[test])[0,1]:.3f}"); [print("   ",v) for v in res.values()]
evaluate("V0 base net efficiency only",0,0,False)
evaluate("V1 trend, NO decay (1.2/0.8)",1.2,0.8,False)
evaluate("V2 trend WITH velocity decay (1.2/0.8)",1.2,0.8,True)
for a,b in ((0.3,0.3),(0.6,0.4),(2.0,1.5)):
    evaluate(f"V3 decay, weights {a}/{b}",a,b,True)
# Monte Carlo over random trend weights (decay on), selected on train, scored on test
rng=np.random.default_rng(3); best=None; rows=[]
for _ in range(300):
    a,b=rng.uniform(-1,3),rng.uniform(-1,3); x=diff(a,b,True); A=np.c_[np.ones(len(H)),x]
    beta=np.linalg.lstsq(A[train],H.margin[train],rcond=None)[0]; p=A@beta
    rows.append((np.mean(np.abs(p[train]-H.margin[train])),np.mean(np.abs(p[test]-H.margin[test])),a,b))
R=pd.DataFrame(rows,columns=["tr","te","a","b"]); sel=R.sort_values("tr").iloc[0]
v0=diff(0,0,False); A0=np.c_[np.ones(len(H)),v0]; b0=np.linalg.lstsq(A0[train],H.margin[train],rcond=None)[0]; p0=A0@b0
print(f"\nRandom-weight search (300 draws): best-on-train weights ({sel.a:.2f},{sel.b:.2f}) -> train MAE {sel.tr:.3f}, test MAE {sel.te:.3f}; baseline V0 test MAE {np.mean(np.abs(p0[test]-H.margin[test])):.3f}; random draws that beat V0 on test: {(R.te<np.mean(np.abs(p0[test]-H.margin[test]))).mean():.1%}")
