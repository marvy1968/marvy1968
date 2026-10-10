import numpy as np, pandas as pd
exec(open("grade_an.py").read().split('print("n"')[0])
rng=np.random.default_rng(7)
H=G.dropna(subset=["over"]).copy()
print("Over games",len(H))
# by-season stability of the top-quintile Under
H["q"]=pd.qcut(H.score_grade,5,labels=False)
print("\nTop scoring-grade quintile by season: n, Over%")
top=H[H.q==4]; print(top.groupby("season").over.agg(["size","mean"]).round(3).to_string())
tr,te=H[H.season<=2021],H[H.season>=2022]
cut=tr.score_grade.quantile(.8)   # threshold fixed on training seasons only
for name,d in (("train 2018-21",tr),("TEST 2022-25",te),("recent 2024-25",H[H.season>=2024])):
    s=d[d.score_grade>=cut]; u=1-s.over.mean(); n=len(s)
    roi=u*(100/110)-(1-u)   # Under at -110
    print(f"{name}: score_grade>={cut:.1f}: n={n}, Under {u:.3f}, ROI at -110 {roi:+.3f}")
# Monte Carlo: shuffle Over labels, how often does the best 'top-quintile-style' cell beat observed (family: 5 quintiles x both sides, plus 16 matchup cells)
def stat(d, labels):
    best=0
    qq=pd.qcut(d.score_grade,5,labels=False).values
    for k in range(5):
        m=qq==k; best=max(best,abs(labels[m].mean()-0.5)*np.sqrt(m.sum()))
    return best
obs=stat(H,H.over.values)
null=[stat(H,rng.permutation(H.over.values)) for _ in range(3000)]
print(f"\nMonte Carlo (3000 shuffles, 5 quintiles tested): best z observed {obs:.2f}, p(max>=obs by chance) = {(np.array(null)>=obs).mean():.4f}")
# random-grade null: replace score_grade with random numbers, repeat the discover-on-train / test-on-holdout pipeline
hits=[]
for _ in range(1000):
    r=rng.random(len(H)); d=H.assign(sg=r)
    trr,ter=d[d.season<=2021],d[d.season>=2022]
    best_cut=None; best_u=0
    for qv in (.7,.8,.9):
        c=trr.sg.quantile(qv); s=trr[trr.sg>=c]; u=1-s.over.mean()
        if u>best_u: best_u,best_cut=u,c
    s=ter[ter.sg>=best_cut]; hits.append(1-s.over.mean())
hits=np.array(hits); te_u=1-te[te.score_grade>=cut].over.mean()
print(f"Random-grade pipeline (discover on train, test on holdout): holdout Under rate mean {hits.mean():.3f}, sd {hits.std():.3f}; real holdout {te_u:.3f} -> beats {(hits<te_u).mean():.1%} of random grades")
# 90 vs 75 matchup specifically
sel=G[(G.hi>=90)&(G.lo>=70)&(G.lo<=80)]
print(f"\n90+ vs 70-80: n={len(sel)}, higher-grade ML {sel.hi_win.mean():.3f} (market fav {sel.mkt_fav_win.mean():.3f}), ATS {sel.hi_cov.mean():.3f}, Over {sel.over.mean():.3f}")
