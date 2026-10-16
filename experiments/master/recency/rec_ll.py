"""RECENCY study for MLB / NHL (no historical lines cached -> no betting backtest possible).
Scores-only check: home-win probability from RW x last-3 + (1-RW) x 400-day run/goal rates (same rate() as rec_bt.py),
log-loss and Brier by window. RW=0 == current Master power rating."""
import glob, json, sys, numpy as np, pandas as pd
from scipy.stats import norm
C = "/opt/marv-bot/state/cache/"; RN = 3; GRID = [0.0, 0.15, 0.3, 0.45, 0.6, 0.75]
SD = dict(mlb=4.2, nhl=1.6); HFA = dict(mlb=0.15, nhl=0.15)
def load(sp):
    rows = []
    for f in sorted(glob.glob(C + f"espn_{sp}_*.json")):
        for e in json.load(open(f)):
            c = e["competitions"][0]
            if not c.get("status", {}).get("type", {}).get("completed") or e.get("season", {}).get("type") != 2: continue
            t = {x["homeAway"]: x for x in c["competitors"]}
            try: rows.append((e["id"], pd.Timestamp(e["date"]).tz_localize(None), t["home"]["team"]["displayName"], t["away"]["team"]["displayName"], float(t["home"]["score"]), float(t["away"]["score"])))
            except Exception: pass
    return pd.DataFrame(rows, columns=["gid", "date", "home", "away", "hs", "as_"]).drop_duplicates("gid").sort_values("date").reset_index(drop=True)
out = {}
for key, sp in (("mlb", "baseball_mlb"), ("nhl", "hockey_nhl")):
    g = load(sp); res = []
    lo = pd.concat([pd.DataFrame({"i": g.index, "date": g.date, "team": g.home, "pf": g.hs, "pa": g.as_}),
                    pd.DataFrame({"i": g.index, "date": g.date, "team": g.away, "pf": g.as_, "pa": g.hs})]).sort_values(["date", "i"])
    feats = {}
    for t, d in lo.groupby("team"):
        n = np.arange(len(d))
        for col in ("pf", "pa"):
            mac = d[col].expanding().mean().shift(1); rec = d[col].rolling(RN, min_periods=1).mean().shift(1)
            for i, a, b, k in zip(d.i, mac, rec, n): feats[(i, t, col)] = (a, b, k)
    for rw in GRID:
        for i, x in g.iterrows():
            f = {}
            for t in (x.home, x.away):
                a = feats[(i, t, "pf")]; b = feats[(i, t, "pa")]
                if a[2] < 3: break
                f[t] = ((1-rw)*a[0]+rw*a[1], (1-rw)*b[0]+rw*b[1])
            if len(f) < 2 or x.hs == x.as_: continue
            m = (f[x.home][0]+f[x.away][1])/2 - (f[x.away][0]+f[x.home][1])/2 + HFA[key]
            p = float(np.clip(norm.cdf(m/SD[key]*1.0), 0.02, 0.98)); y = float(x.hs > x.as_)
            res.append((rw, x.date, -(y*np.log(p)+(1-y)*np.log(1-p)), (p-y)**2))
    R = pd.DataFrame(res, columns=["rw", "date", "ll", "brier"])
    W = {"tune(<2026-08-06)": R.date < "2026-08-06", "W10(08-06..10-14)": (R.date >= "2026-08-06"), "W3(09-24..10-14)": R.date >= "2026-09-24"}
    out[key] = {w: {str(rw): dict(n=int(len(s)), ll=round(s.ll.mean(), 4), brier=round(s.brier.mean(), 4)) for rw, s in R[m].groupby("rw")} for w, m in W.items()}
    print(key, json.dumps(out[key]), flush=True)
json.dump(out, open("ll_mlb_nhl.json", "w"), indent=1)
