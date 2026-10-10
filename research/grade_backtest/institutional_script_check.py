import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from scipy.stats import skewnorm
def inst_hpr(t):
    dw=min(1.0,1.0/(1.0+0.5*abs(t['rolling_off_delta']))); tm=1.0+max(0.0,t['bcr_or_depth']-0.40)*0.45
    return ((t['base_net_epa']+t['rolling_off_delta']*1.2*dw)-t['rolling_def_delta']*0.8)*tm*10
def sim(a,b,move=0.0,n=8000,fixed=False,center=False,seed=0):
    rng=np.random.default_rng(seed); ha,hb=inst_hpr(a),inst_hpr(b)
    ma=27.5+(ha-hb)*0.55; mb=(24.0-(ha-hb)*0.55) if fixed else (24.0-(hb-ha)*0.55)
    al,sd=1.2,11.5
    if center:   # make loc such that the MEAN equals the intended score
        dlt=al/np.sqrt(1+al*al); off=sd*dlt*np.sqrt(2/np.pi); ma-=off; mb-=off
    sa=skewnorm.rvs(al,loc=ma,scale=sd,size=n,random_state=rng); sb=skewnorm.rvs(al,loc=mb,scale=sd,size=n,random_state=rng)
    m,t=sa-sb,sa+sb
    return dict(margin=m.mean()-move*0.15,total=np.median(t),cover=(m>2.5).mean()*100)
tx=dict(base_net_epa=2.05,rolling_off_delta=0.10,rolling_def_delta=-0.12,bcr_or_depth=0.88)
ok=dict(base_net_epa=1.70,rolling_off_delta=-0.05,rolling_def_delta=0.08,bcr_or_depth=0.76)
print("PASTED script, Texas vs Oklahoma      :",{k:round(float(v),1) for k,v in sim(tx,ok,1.5).items()})
print("  teams swapped                       :",{k:round(float(v),1) for k,v in sim(ok,tx,1.5).items()})
print("  Texas vs a very weak team           :",{k:round(float(v),1) for k,v in sim(tx,dict(base_net_epa=-1.0,rolling_off_delta=0,rolling_def_delta=0,bcr_or_depth=.2),0).items()})
print("  skew-normal mean vs intended        : sample mean of one team's score =",round(float(skewnorm.rvs(1.2,loc=27.5,scale=11.5,size=200000,random_state=1).mean()),1),"(intended 27.5)")
# ---- 100 real games
exec(open("hpr_bt.py").read().split("def diff(")[0])
T=pd.read_pickle("talent.pkl").dropna(subset=["talent"]); T["pct"]=T.groupby("season").talent.rank(pct=True)
tp=T.set_index(["season","team"]).pct
H["h_b"]=[tp.get((s,t),np.nan) for s,t in zip(H.season,H.home)]; H["a_b"]=[tp.get((s,t),np.nan) for s,t in zip(H.season,H.away)]
G=H.dropna(subset=["h_b","a_b"]).sort_values("date").tail(100).reset_index(drop=True)
print("\n100 most recent FBS games with lines:",G.date.min().date(),"to",G.date.max().date())
def team(r,side): return dict(base_net_epa=(r[side+"_base_off"]-r[side+"_base_def"])/10,rolling_off_delta=r[side+"_off_delta"]/10,rolling_def_delta=r[side+"_def_delta"]/10,bcr_or_depth=r[side+"_b"])
def batch(label,**kw):
    res=[]
    for i,r in G.iterrows(): res.append(sim(team(r,"h"),team(r,"a"),0.0,**kw))
    R=pd.DataFrame(res); mg,tt=R.margin.values,R.total.values
    # spread comparison: pred margin (home - away) vs actual and vs market
    print(f"{label:34s} margin corr w/ actual {np.corrcoef(mg,G.margin)[0,1]:+.3f} | MAE {np.mean(np.abs(mg-G.margin)):.1f} (market {np.mean(np.abs(G.line-G.margin)):.1f}) | total range {tt.min():.0f}-{tt.max():.0f} (actual totals {G.home_points.add(G.away_points).min():.0f}-{G.home_points.add(G.away_points).max():.0f}) | corr(total,actual total) {np.corrcoef(tt,G.home_points+G.away_points)[0,1]:+.3f}")
    return mg
m1=batch("pasted (sign bug, uncentered skew)",fixed=False,center=False)
m2=batch("sign fixed, skew centered",fixed=True,center=True)
m3=batch("sign fixed, centered, seed 2",fixed=True,center=True,seed=2)
print("seed-to-seed margin difference (fixed version): mean abs %.2f pts, max %.2f"%(np.mean(np.abs(m2-m3)),np.max(np.abs(m2-m3))))
