"""Tune RW on games before 2026-08-06, validate on unseen W10 (08-06..10-14) and W3 (09-24..10-14)."""
import json, glob, pandas as pd, numpy as np
W = {"tune": lambda d: d < "2026-08-06", "W10": lambda d: d >= "2026-08-06", "W3": lambda d: d >= "2026-09-24"}
def sm(b): return dict(n=len(b), units=round(b.units.sum(), 2), roi=round(b.units.sum()/len(b), 3) if len(b) else None)
rep = {}
for f in sorted(glob.glob("bets_*.csv")):
    B = pd.read_csv(f, parse_dates=["date"]); sp = f[5:-4]; r = {}
    for rw, b in B.groupby("rw"): r[str(rw)] = {w: sm(b[fn(b.date.astype(str))]) for w, fn in W.items()}
    tune = {k: v["tune"] for k, v in r.items() if v["tune"]["n"] >= 30}
    best = max(tune, key=lambda k: tune[k]["units"]); base = r["0.0"]
    def beats(k): return k != "0.0" and all(r[k][w]["units"] > base[w]["units"] for w in ("W10", "W3"))
    rep[sp] = dict(by_rw=r, tuned_rw=float(best), tuned_beats_master_unseen=beats(best), pdf_0p6_beats_master_unseen=beats("0.6"),
                   adopt=float(best) if beats(best) else None)
    print(sp, "tuned", best, {w: r[best][w] for w in W}, "| master", {w: base[w] for w in W}, "| pdf0.6", {w: r["0.6"][w] for w in W}, "ADOPT" if beats(best) else "track")
json.dump(rep, open("report.json", "w"), indent=1)
