import numpy as np
def sim(team_a, team_b, sims=20000, fixed=False, decay=True, seed=1):
    rng=np.random.default_rng(seed)
    def hpr(t):
        dw=min(1.0,1.0/(1.0+0.5*abs(t['rolling_off_delta']))) if decay else 1.0
        tm=(1.0+max(0.0,t['bcr']-0.40)*0.4)*t['depth_factor']
        return ((t['base_net_epa']+t['rolling_off_delta']*1.2*dw)-t['rolling_def_delta']*0.8)*tm*10
    a,b=hpr(team_a),hpr(team_b)
    ma=27.5+(a-b)*0.55
    mb=(24.0-(a-b)*0.55) if fixed else (24.0-(b-a)*0.55)      # pasted: 24 - (hpr_b - hpr_a)*0.55
    sa,sb=rng.normal(ma,11.5,sims),rng.normal(mb,11.5,sims)
    return dict(mean_a=ma,mean_b=mb,margin=(sa-sb).mean(),total=np.median(sa+sb),winA=(sa>sb).mean()*100)
g=dict(name='Georgia',base_net_epa=1.85,rolling_off_delta=-0.15,rolling_def_delta=-0.05,bcr=0.84,depth_factor=1.02)
al=dict(name='Alabama',base_net_epa=1.98,rolling_off_delta=0.22,rolling_def_delta=-0.18,bcr=0.89,depth_factor=1.05)
print("PASTED script, Georgia vs Alabama:",{k:round(v,2) for k,v in sim(g,al).items()})
print("Flip the teams            :",{k:round(v,2) for k,v in sim(al,g).items()})
weak=dict(name='Mid',base_net_epa=-1.0,rolling_off_delta=0,rolling_def_delta=0,bcr=0.30,depth_factor=0.95)
print("Georgia vs a MUCH weaker team (pasted):",{k:round(v,2) for k,v in sim(g,weak).items()})
print("Georgia vs a MUCH weaker team (sign fixed):",{k:round(v,2) for k,v in sim(g,weak,fixed=True).items()})

rng=np.random.default_rng(42)
def mk(top):
    return dict(name="T" if top else "M",
                base_net_epa=rng.normal(1.2,0.4) if top else rng.normal(0.2,0.4),
                rolling_off_delta=rng.normal(0,0.35), rolling_def_delta=rng.normal(0,0.25),
                bcr=rng.uniform(0.70,0.92) if top else rng.uniform(0.15,0.44),
                depth_factor=rng.uniform(0.95,1.07) if top else rng.uniform(0.90,1.03))
M=[(mk(True),mk(False)) for _ in range(50)]
def run(label,**kw):
    up=0; marg=[]
    for t,m in M:
        r=sim(t,m,sims=4000,**kw); marg.append(r['margin']); up+= r['winA']<50
    print(f"{label:46s} model favors the MID-tier team in {up}/50 matchups; mean margin for top-tier {np.mean(marg):+.1f}")
run("pasted script (sign bug), decay on")
run("pasted script (sign bug), decay off",decay=False)
run("sign fixed, decay ON",fixed=True)
run("sign fixed, decay OFF",fixed=True,decay=False)
# heavy noise: force big rolling slump on the top team (injury spike)
for t,m in M: t['rolling_off_delta']=-abs(rng.normal(1.2,0.4)); t['rolling_def_delta']=abs(rng.normal(0.6,0.2))
print("\nTop team in a severe 3-game slump (off delta ~ -1.2, def delta ~ +0.6):")
run("sign fixed, decay ON",fixed=True)
run("sign fixed, decay OFF",fixed=True,decay=False)
