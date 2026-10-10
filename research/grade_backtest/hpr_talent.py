import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("hpr_bt.py").read().split("def diff(")[0])
T=pd.read_pickle("talent.pkl").dropna(subset=["talent"])
T["tz"]=T.groupby("season").talent.transform(lambda s:(s-s.mean())/s.std())
tm=T.set_index(["season","team"]).tz
def tz(col,team): return pd.Series([tm.get((s,t),np.nan) for s,t in zip(H.season,H[team])])
H["h_tz"]=tz("season","home").values; H["a_tz"]=tz("season","away").values
print("talent coverage:",H.h_tz.notna().mean().round(3),H.a_tz.notna().mean().round(3))
H=H.dropna(subset=["h_tz","a_tz"]).reset_index(drop=True)
train=H.season<=2022; test=H.season>=2023
def hp(side,aoff,adef,decay,c):
    d=pd.DataFrame({"base_off":H[side+"_base_off"],"base_def":H[side+"_base_def"],"off_delta":H[side+"_off_delta"],"def_delta":H[side+"_def_delta"]})
    tmv=1+c*H[side+"_tz"]
    return hpr(d,aoff,adef,decay)*tmv
def ev(name,aoff,adef,decay,c,extra=False):
    x=(hp("h",aoff,adef,decay,c)-hp("a",aoff,adef,decay,c)).values
    cols=[np.ones(len(H)),x]+([ (H.h_tz-H.a_tz).values ] if extra else [])
    A=np.c_[tuple(cols)]; b=np.linalg.lstsq(A[train],H.margin[train],rcond=None)[0]; p=A@b
    mae=np.mean(np.abs(p[test]-H.margin[test])); mk=np.mean(np.abs(H.line[test]-H.margin[test]))
    e=p[test]-H.line[test]; ats=np.sign(H.margin[test]+H.spread[test]); out=f"{name:46s} MAE {mae:.2f} (mkt {mk:.2f}) corr {np.corrcoef(p[test],H.margin[test])[0,1]:.3f}"
    for th in (2,4):
        k=(np.abs(e)>=th)&(ats!=0); out+=f" | edge>={th}: ATS {(np.sign(e[k])==ats[k]).mean():.3f} n={int(k.sum())}"
    print(out)
print("test games",test.sum())
ev("V0 base net efficiency",0,0,False,0)
ev("V2 trend+decay (1.2/0.8), TM=1",1.2,0.8,True,0)
for c in (0.03,0.06,0.10): ev(f"V2 + talent multiplier 1+{c}*z",1.2,0.8,True,c)
for c in (0.03,0.06,0.10): ev(f"V0 + talent multiplier 1+{c}*z",0,0,False,c)
ev("V0 + talent as separate regressor",0,0,False,0,extra=True)
ev("V2 + talent as separate regressor",1.2,0.8,True,0,extra=True)
# talent alone
x=(H.h_tz-H.a_tz).values; A=np.c_[np.ones(len(H)),x]; b=np.linalg.lstsq(A[train],H.margin[train],rcond=None)[0]; p=A@b
print(f"talent difference alone: MAE {np.mean(np.abs(p[test]-H.margin[test])):.2f}, corr {np.corrcoef(p[test],H.margin[test])[0,1]:.3f}")
# does talent explain the market's miss? regress (margin - line) on talent diff
r=(H.margin-H.line)[test]; xx=(H.h_tz-H.a_tz)[test]; print("corr(talent diff, margin-market line) on test: %.3f (n=%d)"%(np.corrcoef(xx,r)[0,1],test.sum()))
