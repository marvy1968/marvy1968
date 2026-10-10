import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from sklearn.linear_model import LogisticRegression
rng=np.random.default_rng(5)
def pay(ml): return np.where(ml>0,ml/100,100/-ml)
def imp(ml): return np.where(ml>0,100/(ml+100),-ml/(-ml+100))
def logit(p): p=np.clip(p,1e-4,1-1e-4); return np.log(p/(1-p))
def ll(y,p): p=np.clip(p,1e-4,1-1e-4); return -np.mean(y*np.log(p)+(1-y)*np.log(1-p))
# ---------------- NFL: dog moneylines ----------------
n=pd.read_pickle("nfl_v1.pkl"); nv=pd.read_csv("state/cache/nflverse_games.csv",low_memory=False)
nv=nv[["game_id","home_rest","away_rest","temp","wind","roof","div_game"]]
d=n.merge(nv,on="game_id").dropna(subset=["h_points","a_points","home_ml","away_ml"]).copy()
d=d[d.h_points!=d.a_points]; d["season"]=d.season.astype(int)
ph=imp(d.home_ml.values); pa=imp(d.away_ml.values); fair_h=ph/(ph+pa)
d["dog_home"]=(fair_h<0.5).astype(int)
d["p_mkt"]=np.where(d.dog_home==1,fair_h,1-fair_h)                  # market no-vig prob the dog wins
d["y"]=np.where(d.dog_home==1,d.h_points>d.a_points,d.a_points>d.h_points).astype(int)
d["ml"]=np.where(d.dog_home==1,d.home_ml,d.away_ml); d["impl"]=imp(d.ml.values)
mm=(d.h_consensus-d.a_consensus)                                   # Marv margin (home view)
mkt=-d.spread                                                       # market margin (home view)
d["gap"]=np.where(d.dog_home==1,(mm-mkt),-(mm-mkt))                # Marv thinks dog is better by this many pts vs the market
d["rest"]=np.where(d.dog_home==1,d.home_rest-d.away_rest,d.away_rest-d.home_rest).clip(-7,7)
d["wind"]=d.wind.fillna(0); d["temp"]=d.temp.fillna(70); d["dome"]=d.roof.isin(["dome","closed"]).astype(int); d["div"]=d.div_game.fillna(0)
d["lm"]=logit(d.p_mkt.values)
FEATS=["gap","rest","dog_home","wind","temp","dome","div"]
def walk(d,feats,label):
    out=[]
    for s in sorted(d.season.unique())[3:]:
        tr,te=d[d.season<s],d[d.season==s]
        base=LogisticRegression(C=10).fit(tr[["lm"]],tr.y); full=LogisticRegression(C=0.3).fit(tr[["lm"]+feats],tr.y)
        te=te.assign(p0=base.predict_proba(te[["lm"]])[:,1],p1=full.predict_proba(te[["lm"]+feats])[:,1]); out.append(te)
    R=pd.concat(out); y=R.y.values
    print(f"\n{label}: test games {len(R)}, seasons {R.season.min()}-{R.season.max()}, dog wins {y.mean():.3f}")
    print(f"  log-loss raw market {ll(y,R.p_mkt.values):.4f} | calibrated market {ll(y,R.p0.values):.4f} | market+features {ll(y,R.p1.values):.4f}")
    allb=np.where(y==1,pay(R.ml.values),-1); print(f"  flat bet EVERY dog: ROI {allb.mean():+.3f} (n={len(R)})")
    for nm,p in (("calibrated market only","p0"),("market + features","p1")):
        for th in (0.03,0.05,0.08):
            k=(R[p]-R.impl)>=th; 
            if k.sum()<10: continue
            r=np.where(R.y[k]==1,pay(R.ml[k].values),-1); lo,hi=np.percentile([rng.choice(r,len(r)).mean() for _ in range(2000)],[2.5,97.5])
            print(f"  {nm:24s} edge>={th:.2f}: n={int(k.sum()):4d} hit {R.y[k].mean():.3f} ROI {r.mean():+.3f} [{lo:+.2f},{hi:+.2f}]")
    return R
R=walk(d,FEATS,"NFL underdog moneyline (consensus close prices)")
full=LogisticRegression(C=0.3).fit(d[["lm"]+FEATS],d.y); print("  full-fit coefficients:",dict(zip(["market logit"]+FEATS,np.round(full.coef_[0],3))))
# permutation: shuffle the feature rows, same walk, count how often feature model ROI at edge>=.05 beats observed
def roi_at(R,th=0.05):
    k=(R.p1-R.impl)>=th; return np.where(R.y[k]==1,pay(R.ml[k].values),-1).mean() if k.sum()>=10 else np.nan
obs=roi_at(R); null=[]
for _ in range(100):
    dd=d.copy(); dd[FEATS]=dd[FEATS].sample(frac=1,random_state=int(rng.integers(1e9))).values
    out=[]
    for s in sorted(dd.season.unique())[3:]:
        tr,te=dd[dd.season<s],dd[dd.season==s]; f=LogisticRegression(C=0.3).fit(tr[["lm"]+FEATS],tr.y); out.append(te.assign(p1=f.predict_proba(te[["lm"]+FEATS])[:,1]))
    null.append(roi_at(pd.concat(out)))
null=np.array([x for x in null if not np.isnan(x)]); print(f"  permutation (100 shuffles of features): observed ROI@5% {obs:+.3f}; shuffled mean {null.mean():+.3f}; observed beats {np.mean(null<obs):.0%} of shuffles")
# ---------------- CFB: underdog covers at Bovada spread odds ----------------
c=pd.read_pickle("cfb_lines_model.pkl").dropna(subset=["spread_bovada","bov_home_odds","bov_away_odds","home_points","away_points"]).copy()
c=c[c.home_fbs&c.away_fbs] if "home_fbs" in c else c
c["margin"]=c.home_points-c.away_points; sp=c.spread_bovada          # Bovada home spread (negative = home favored)
c=c[(c.margin+c.spread_bovada)!=0].copy(); sp=c.spread_bovada; c["dog_home"]=(sp>0).astype(int)
c["y"]=np.where(c.dog_home==1,c.margin+sp>0,c.margin+sp<0).astype(int)  # dog covers
c["price"]=np.where(c.dog_home==1,c.bov_home_odds,c.bov_away_odds); c["impl"]=imp(c.price.values)
c["lm"]=logit(np.where(c.dog_home==1,imp(c.bov_home_odds.values)/(imp(c.bov_home_odds.values)+imp(c.bov_away_odds.values)),imp(c.bov_away_odds.values)/(imp(c.bov_home_odds.values)+imp(c.bov_away_odds.values))))
o=lambda x: np.where(c.dog_home==1,x,-x)
c["gapm"]=o((c.mdl_margin.fillna(-c.spread_close)+c.spread_close).values) if "mdl_margin" in c else 0   # Marv vs close
c["move"]=o((c.spread_open-c.spread_bovada).fillna(0).values)           # + = line moved toward the dog
c["bovsharp"]=o((c.spread_sharp-c.spread_bovada).fillna(0).values)      # + = Bovada gives dog more points than sharp
c["absp"]=np.abs(sp); c["season"]=c.season.astype(int)
CF=["gapm","move","bovsharp","absp","dog_home"]
c[CF]=c[CF].fillna(0)
print(f"\nCFB Bovada spread data: games {len(c)}, seasons {c.season.min()}-{c.season.max()}, dog covers {c.y.mean():.3f}")
out=[]
for s in sorted(c.season.unique())[2:]:
    tr,te=c[c.season<s],c[c.season==s]
    if len(tr)<300 or len(te)<50: continue
    f=LogisticRegression(C=0.3).fit(tr[["lm"]+CF],tr.y); out.append(te.assign(p1=f.predict_proba(te[["lm"]+CF])[:,1]))
Q=pd.concat(out); y=Q.y.values; print(f"  test games {len(Q)} ({Q.season.min()}-{Q.season.max()}): log-loss market-implied {ll(y,Q.impl.values):.4f}, +features {ll(y,Q.p1.values):.4f}")
for th in (0.02,0.04,0.06):
    k=(Q.p1-Q.impl)>=th
    if k.sum()<20: continue
    r=np.where(Q.y[k]==1,pay(Q.price[k].values),-1); lo,hi=np.percentile([rng.choice(r,len(r)).mean() for _ in range(2000)],[2.5,97.5])
    print(f"  dog +pts at Bovada, edge>={th:.2f}: n={int(k.sum()):4d} cover {Q.y[k].mean():.3f} ROI {r.mean():+.3f} [{lo:+.2f},{hi:+.2f}]")
k=Q.bovsharp>=0.5; r=np.where(Q.y[k]==1,pay(Q.price[k].values),-1); print(f"  rule: Bovada gives dog 0.5+ more points than sharp: n={int(k.sum())} cover {Q.y[k].mean():.3f} ROI {r.mean():+.3f}")
