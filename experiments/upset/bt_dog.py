"""Model-edge underdog backtest (paper research, 2026-10-13).
P_model = Master-style: 50/50 rating+market shrink (analytic normal == MC mean, no RNG) -> logit + K*(dTCI+dPPE) -> +TF_side (bt_tf.py definitions).
edge = P_model(dog) - vig-free implied(dog). Dog ML ROI by edge bucket / price bucket / window. Plus confirmation tags + logistic OOS."""
import numpy as np, pandas as pd, json, sys, warnings; warnings.filterwarnings("ignore")
from scipy.stats import norm
sys.argv = ["x", "2019-01-01"]
src = open("/opt/marv-bot/experiments/tci_ppe_uf/bt_tf.py").read().split('if __name__ == "__main__":')[0]
src = src.replace("g.season >= 2025", "g.season >= 2020").replace('nfl_stats_team_week_{s}.csv") for s in (2025, 2026)', 'nfl_stats_team_week_{s}.csv") for s in range(2020, 2027)').replace('wnba_box_{s}.parquet") for s in (2025, 2026)', 'wnba_box_{s}.parquet") for s in range(2018, 2027)')
ns = {}; exec(src, ns)
def imp(a):
    a = np.asarray(a, float); return np.where(a > 0, 100/(np.abs(a)+100), np.abs(a)/(np.abs(a)+100))
def pay(a): return np.where(a > 0, a/100, 100/-a)
K, TFMAX, TFN = 0.10, 0.025, 4

def probs(name, df, R, P):
    fin = df.dropna(subset=["hs", "as_", "hml", "aml"]).sort_values("date").reset_index(drop=True)
    allf = df.dropna(subset=["hs", "as_"]).sort_values("date").reset_index(drop=True)
    Rk = R.set_index(["gid", "team"])[["tci", "ppe"]]; Rk = Rk[~Rk.index.duplicated()]
    out = []
    for _, x in fin.iterrows():
        past = allf[(allf.date < x.date) & (allf.date >= x.date - pd.Timedelta(days=400))]
        def rate(t):
            a = past[past.home == t]; b = past[past.away == t]; n = len(a)+len(b)
            if n < 3: return None
            return (a.hs.sum()+b.as_.sum())/n, (a.as_.sum()+b.hs.sum())/n
        rh, ra = rate(x.home), rate(x.away)
        if not rh or not ra or pd.isna(x.spread) or pd.isna(x.total): continue
        Lg = (past.hs.mean()+past.as_.mean())/2; hfa = 0 if x.neutral else P["hfa"]
        eh = rh[0]+ra[1]-Lg+hfa/2; ea = ra[0]+rh[1]-Lg-hfa/2
        mh, ma = (x.total-x.spread)/2, (x.total+x.spread)/2; eh, ea = 0.5*eh+0.5*mh, 0.5*ea+0.5*ma
        ph = norm.cdf((eh-ea)/P["sd_m"])
        def z(t):
            try: return Rk.loc[(x.gid, t)]
            except KeyError: return pd.Series({"tci": 0.0, "ppe": 0.0})
        zh, za = z(x.home), z(x.away); dT, dP = float(zh.tci-za.tci), float(zh.ppe-za.ppe)
        def trend(t):
            a = past[((past.home == t) | (past.away == t)) & past.spread.notna()].tail(TFN)
            if len(a) < 2: return 0.0
            return float(np.nanmean(np.where(a.home == t, (a.hs-a.as_)+a.spread, (a.as_-a.hs)-a.spread)))
        th, ta = trend(x.home), trend(x.away)
        tf = float(np.clip(TFMAX*np.tanh((th-ta)/P["sd_m"]), -TFMAX, TFMAX))
        def last(t):
            a = past[(past.home == t) | (past.away == t)].tail(1)
            if a.empty: return 0.0, 7.0
            r = a.iloc[0]; return ((r.hs-r.as_) if r.home == t else (r.as_-r.hs)), float((x.date-r.date).days)
        (lmh, rsh), (lma, rsa) = last(x.home), last(x.away)
        ph_m = float(np.clip(ns["sig"](ns["logit"](ph) + K*(dT+dP)) + tf, 0.001, 0.999))
        ih, ia = imp(x.hml), imp(x.aml); vh = ih/(ih+ia)
        hdog = x.hml > x.aml
        s = 1 if hdog else -1
        out.append(dict(sport=name, gid=x.gid, date=x.date, season=x.date.year if name != "nfl" or x.date.month > 2 else x.date.year-1,
                        dog=x.home if hdog else x.away, fav=x.away if hdog else x.home, dog_home=int(hdog),
                        ml=x.hml if hdog else x.aml, oml=x.aml if hdog else x.hml, won=float((x.hs > x.as_) if hdog else (x.as_ > x.hs)),
                        push=x.hs == x.as_, p_mkt=vh if hdog else 1-vh, p_model=ph_m if hdog else 1-ph_m,
                        dspread=x.spread*s, d_tci=(dT if hdog else -dT), d_ppe=(dP if hdog else -dP), d_tf=(th-ta)*s,
                        d_last=(lmh-lma)*s, d_rest=(rsh-rsa)*s, chaos=bool(x.rival) or min(lmh, lma) <= -P["blow"]))
    return pd.DataFrame(out)

frames = []
for nm in ("nfl", "wnba"):
    df, R, P = ns[nm](); d = probs(nm, df, R, P); frames.append(d); print(nm, len(d), d.date.min(), d.date.max(), flush=True)
D = pd.concat(frames); D = D[~D.push]; D["edge"] = D.p_model - D.p_mkt
D.to_csv("/opt/marv-bot/experiments/upset/dog_probs.csv", index=False)
print("saved")
