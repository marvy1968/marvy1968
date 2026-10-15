import json, numpy as np
from bt import *
p = json.load(open("params.json")); rows = run(p, sims=5000)
ESPN = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA"}
OD = {}
for o in json.load(open("data/odds.json")).values():
    OD[(o["date"], ESPN.get(o["home"], o["home"]), ESPN.get(o["away"], o["away"]))] = o
from datetime import date, timedelta
def find(g):
    for d in (g["date"], str(date.fromisoformat(g["date"]) - timedelta(1)), str(date.fromisoformat(g["date"]) + timedelta(1))):
        o = OD.get((d, g["home"], g["away"]))
        if o: return o
X = []
for r in rows:
    if r["s"] != 2025: continue
    o = find(r["g"])
    if o and o.get("hml") and o.get("aml"): X.append((r, o))
print("2025-26 games with lines", len(X), "of", sum(r["s"] == 2025 for r in rows))
def bets(Xs, ml_thr, tot_thr, pl_thr, uf=True):
    out = []
    for r, o in Xs:
        f, m, g = r["f"], r["m"], r["g"]
        if uf and f["uf"] == 0: continue
        ph = float(np.clip(m["home"] + f["tf"], .02, .98))
        ih, ia = ml_prob(o["hml"]), ml_prob(o["aml"]); mh = ih / (ih + ia)
        hg, ag = g["hg"], g["ag"]
        for side, pp, mp, a, win in (("H", ph, mh, o["hml"], hg > ag), ("A", 1 - ph, 1 - mh, o["aml"], ag > hg)):
            if pp - mp >= ml_thr and pp * dec(a) - 1 > 0:
                out.append(("ml", dec(a) - 1 if win else -1.0, g["date"]))
        if o.get("total") and o.get("over") and o.get("under") and tot_thr is not None:
            L = float(o["total"]); mu = float(m["totals"].mean()); T = hg + ag
            if abs(mu - L) >= tot_thr:
                po = p_over(m["totals"], L); side = mu > L
                a = o["over"] if side else o["under"]; pw = po if side else 1 - po
                if pw * dec(a) - 1 > 0:
                    res = 0.0 if T == L else (dec(a) - 1 if (T > L) == side else -1.0)
                    out.append(("tot", res, g["date"]))
        if o.get("spread") is not None and o.get("hpl") and o.get("apl") and pl_thr is not None:
            sp = float(o["spread"])  # home spread
            ph15 = m["h_m15"] if sp < 0 else 1 - m["a_m15"]; ihp, iap = ml_prob(o["hpl"]), ml_prob(o["apl"]); mhp = ihp / (ihp + iap)
            margin = hg - ag
            hcov = margin + sp > 0
            for pp, mp, a, w in ((ph15, mhp, o["hpl"], hcov), (1 - ph15, 1 - mhp, o["apl"], not hcov)):
                if pp - mp >= pl_thr and pp * dec(a) - 1 > 0:
                    out.append(("pl", dec(a) - 1 if w else -1.0, g["date"]))
    return out
def summ(b, k):
    x = [v for t, v, _ in b if t == k]
    if not x: return f"{k}: 0 bets"
    w = sum(v > 0 for v in x); l = sum(v < 0 for v in x)
    return f"{k}: {w}-{l} ({len(x)} bets) units {sum(x):+.2f} ROI {100*sum(x)/len(x):+.1f}%"
tune = [x for x in X if x[0]["g"]["date"] < "2026-01-01"]; test = [x for x in X if x[0]["g"]["date"] >= "2026-01-01"]
# Brier/log vs market
def cmp(Xs):
    y = np.array([r["hw"] for r, o in Xs]); pm = np.array([ml_prob(o["hml"]) / (ml_prob(o["hml"]) + ml_prob(o["aml"])) for r, o in Xs])
    pq = np.clip([r["m"]["home"] + r["f"]["tf"] for r, o in Xs], .02, .98)
    L = lambda p: float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    return f"model logloss {L(pq):.4f} vs market {L(pm):.4f} (n={len(y)})"
print("TUNE Oct-Dec:", cmp(tune)); print("TEST Jan-Jun:", cmp(test))
res = {}
for k, grid in (("ml", [0.02, 0.03, 0.04, 0.05, 0.06, 0.08]), ("tot", [0.3, 0.4, 0.5, 0.6, 0.75]), ("pl", [0.03, 0.04, 0.06, 0.08])):
    best = None
    for t in grid:
        args = dict(ml_thr=t if k == "ml" else 9, tot_thr=t if k == "tot" else None, pl_thr=t if k == "pl" else None)
        b = [v for v in bets(tune, **args) if v[0] == k]; u = sum(v for _, v, _ in b)
        print("tune", k, t, summ(b, k))
        if len(b) >= 25 and (best is None or u / len(b) > best[1]): best = (t, u / len(b))
    res[k] = best[0] if best else grid[-1]
print("chosen on Oct-Dec only:", res)
bt = bets(test, res["ml"], res["tot"], res["pl"])
print("UNSEEN TEST Jan-Jun 2026:")
for k in ("ml", "tot", "pl"): print("  ", summ(bt, k))
json.dump(res, open("thresholds.json", "w"))
# no-filter baselines on test
print("baseline every UF ML edge>0:", summ(bets(test, 0.0, None, None), "ml"))
print("UF off, chosen ml thr:", summ(bets(test, res["ml"], None, None, uf=False), "ml"))
