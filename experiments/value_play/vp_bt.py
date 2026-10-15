import pandas as pd, numpy as np, itertools, json
def imp(a): return 100/(a+100) if a > 0 else -a/(-a+100)
def pay(a): return a/100 if a > 0 else 100/-a
def lg(p): p = np.clip(p, 1e-6, 1-1e-6); return np.log(p/(1-p))
KEYS = (3, 7, 10)
def prep(f):
    d = pd.read_csv(f, parse_dates=["date"])
    d["nv"] = [imp(a)/(imp(a)+imp(b)) for a, b in zip(d.price, d.oprice)]
    d["B"] = d.price.map(pay)
    d["u"] = np.where(d.res > 0, d.B, np.where(d.res < 0, -1.0, 0.0))
    sl = np.where(d.side > 0, d.line, -d.line)          # side's own handicap (+ = getting points)
    d["sline"] = sl
    d["sproj"] = np.where(d.side > 0, d.proj_m, -d.proj_m)  # side's projected margin
    d["home_side"] = d.side > 0
    # key-number cross: market says side loses/wins by X, Marv margin lands on the other side of 3/7/10
    cross = np.zeros(len(d), bool)
    for k in KEYS:
        for s in (k, -k):
            lo, hi = np.minimum(-sl, d.sproj), np.maximum(-sl, d.sproj)
            cross |= (lo < s) & (hi > s)
    d["cross"] = cross & (d.market == "spread")
    d["hook"] = d.market.eq("spread") & np.isin(np.abs(sl), [2.5, 3.5, 6.5, 7.5, 9.5, 10.5]) & (sl > 0)
    d["dd_dog"] = d.market.eq("spread") & (sl >= 10)
    d["home_dog"] = d.market.eq("spread") & d.home_side & (sl > 0) & (sl < 10)
    fav_ml = d.market.eq("ml") & (d.oprice <= -250)       # opponent heavy fav -> this side fades it
    d["fade_fav"] = fav_ml
    d["ext"] = d.market.eq("total") & (np.abs(d.proj_t - d.line) >= 3) & (np.sign(d.proj_t - d.line) == np.sign(d.side))
    return d
def pick(d, w, thr, rule, cap=0.02):
    P = 1/(1+np.exp(-(w*lg(d.p)+(1-w)*lg(d.nv))))
    ev = P*d.B-(1-P); k = np.minimum(np.maximum((d.B*P-(1-P))/d.B, 0)*0.25, cap)
    m = (ev > thr) & (d.uf == 1)
    side_ok = d.market.isin(["spread", "ml"]) & (d[rule] if rule != "none" else True)
    tot_ok = d["ext"]
    m &= (side_ok | tot_ok)
    x = d[m].assign(ev=ev[m], P=P[m], k=k[m])
    return x.sort_values("ev", ascending=False).drop_duplicates(["gid", "market"])
def st(x, stake=False):
    if x.empty: return dict(n=0)
    u = x.u*(x.k/0.02) if stake else x.u
    w, l = int((x.u > 0).sum()), int((x.u < 0).sum())
    return dict(n=len(x), W=w, L=l, units=round(float(u.sum()), 2), roi=round(float(u.sum()/((x.k/0.02).sum() if stake else len(x))), 3))
def baseline(d):  # current Marv (bt_tf 'on'): best side with p - breakeven >= 3%, all markets, UF for cfb only
    e = d.p - d.price.map(imp); m = (e >= 0.03) & ~((d.sport == "cfb") & (d.uf == 0))
    return d[m].assign(e=e[m]).sort_values("e", ascending=False).drop_duplicates(["gid", "market"])
if __name__ == "__main__":
    tr, te = prep("cands_train.csv"), prep("cands_test.csv")
    rules = ["none", "cross", "hook", "dd_dog", "home_dog", "fade_fav"]
    grid = []
    for w, thr, r in itertools.product([0.25, 0.5, 0.75, 1.0], [0.03, 0.045, 0.06, 0.08], rules):
        s = st(pick(tr, w, thr, r))
        if s["n"] >= 40: grid.append((s["roi"], s["n"], w, thr, r))
    grid.sort(reverse=True)
    print("TRAIN top 8:"); [print(" ", g) for g in grid[:8]]
    # choose: best TRAIN ROI with n>=40 per rule family; overall best chosen ONLY on train
    best = grid[0]; w, thr, r = best[2], best[3], best[4]
    print("chosen (train only): w=%s thr=%s rule=%s" % (w, thr, r))
    out = {"chosen": dict(w=w, thr=thr, rule=r)}
    for nm, a, b in (("10wk", "2026-08-01", "2026-10-13"), ("3wk", "2026-09-21", "2026-10-13")):
        T = te[(te.date >= a) & (te.date < b)]
        vp, bl = pick(T, w, thr, r), baseline(T)
        out[nm] = dict(value_play=st(vp), value_play_kelly=st(vp, True), current_marv=st(bl),
                       vp_by_mkt={m: st(vp[vp.market == m]) for m in ("spread", "ml", "total")})
        print(nm, json.dumps(out[nm]))
    # each candidate rule from the example, at chosen w/thr, train vs both test windows
    out["rules"] = {}
    for rr in rules:
        o = {"train": st(pick(tr, w, thr, rr))}
        for nm, a in (("10wk", "2026-08-01"), ("3wk", "2026-09-21")):
            o[nm] = st(pick(te[te.date >= a], w, thr, rr))
        out["rules"][rr] = o; print("rule", rr, o)
    out["spec_raw"] = {nm: st(pick(te[te.date >= a], 1.0, 0.045, "cross")) for nm, a in (("10wk", "2026-08-01"), ("3wk", "2026-09-21"))}
    print("Marvin's raw spec (no shrink, EV>4.5%, key cross):", out["spec_raw"])
    json.dump(out, open("vp_result.json", "w"), indent=1)
