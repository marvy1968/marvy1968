import json, sys, itertools, numpy as np
from model import *
G = sorted(json.load(open("data/games.json")).values(), key=lambda g: (g["start"] or g["date"], g["id"]))
ODDS = {(o["date"], o["home"], o["away"]): o for o in json.load(open("data/odds.json")).values()}
ESPN = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA", "WSH": "WSH"}
def season(g): y, m = int(g["date"][:4]), int(g["date"][5:7]); return y if m >= 8 else y - 1
def run(p, sims=0, odds=False):
    R = Ratings(p); rows = []
    for g in G:
        R.new_season(season(g))
        f = R.features(g)
        hw = 1 if g["hg"] > g["ag"] else 0
        row = dict(g=g, s=season(g), f=f, hw=hw)
        if sims:
            m = simulate(f["lh"], f["la"], p, sims, np.random.default_rng(g["id"] % 100000))
            row["m"] = m
        else:
            # fast analytic approx for tuning
            from math import exp
            row["ph"] = None
        rows.append(row); R.update(g)
    return rows
def ll(rows, ss):
    xs = [r for r in rows if r["s"] in ss]
    p = np.clip([r["m"]["home"] + r["f"]["tf"] for r in xs], .02, .98); y = np.array([r["hw"] for r in xs])
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), len(xs)
if __name__ == "__main__":
    best = None
    for dcy, ws, hm in itertools.product([0.97, 0.98, 0.99], [0.3, 0.55, 0.8], [0.03, 0.05]):
        p = dict(DEFAULT, decay=dcy, w_shot=ws, home=hm)
        rows = run(p, sims=800)
        tr = ll(rows, {2022, 2023}); va = ll(rows, {2024})
        print(dcy, ws, hm, "train", round(tr[0], 4), "val24-25", round(va[0], 4), flush=True)
        if best is None or tr[0] < best[0]: best = (tr[0], p)
    json.dump(best[1], open("params.json", "w")); print("chosen on 2022-24 train:", best)
