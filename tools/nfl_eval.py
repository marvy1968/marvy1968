"""Grade nfl_h2h_bt_picks.csv against Bovada (2022-26) and nflverse closing lines (2016-26)."""
import sys, numpy as np, pandas as pd
df = pd.read_csv(sys.argv[1]); df = df[df.hp != df.ap].copy()
def imp(p): return np.where(p > 0, 100 / (p + 100), -p / (-p + 100))
def pay(p): return np.where(p > 0, p / 100, 100 / -p)
L = []
def P(s=""): L.append(s)
df["ph"] = df.pick == "H"; df["won"] = np.where(df.ph, df.hp > df.ap, df.ap > df.hp)
df["conf"] = np.maximum(df.p_home, 1 - df.p_home); df["mside"] = df.p_home >= .5
df["mwon"] = np.where(df.mside, df.hp > df.ap, df.ap > df.hp)
df["close"] = df.margin.abs() < 3
def season_str(s, col):
    return ", ".join(f"{k}:{g[col].mean():+.0%}/{len(g)}" for k, g in s.groupby("season"))
for book in ("b", "n"):
    name = "BOVADA pregame (2022-26)" if book == "b" else "nflverse CLOSE (2016-26, its juice)"
    P(f"## {name}")
    m = df.dropna(subset=[f"{book}_hml", f"{book}_aml"]).copy()
    ih, ia = imp(m[f"{book}_hml"]), imp(m[f"{book}_aml"]); m["mkt"] = ih / (ih + ia)
    y = (m.hp > m.ap).astype(float)
    P(f"Brier (n={len(m)}): ratings model {((m.p_home - y) ** 2).mean():.4f} vs no-vig market {((m.mkt - y) ** 2).mean():.4f}")
    def ml(mask, pickcol, wincol, label):
        s = m[mask]; price = np.where(s[pickcol], s[f"{book}_hml"], s[f"{book}_aml"])
        prof = np.where(s[wincol], pay(price), -1.0); s = s.assign(profit=prof)
        P(f"  {label}: n={len(s)} win {s[wincol].mean():.1%} ROI {prof.mean():+.1%} | seasons {season_str(s, 'profit')}")
    ml(m.index == m.index, "ph", "won", "H2H pick (sweep/star/margin) all games")
    for meth in ("sweep", "star", "margin"):
        ml(m.method == meth, "ph", "won", f"  method={meth}")
    ml(m.close, "ph", "won", "CLOSE games (|margin|<3) H2H pick")
    ml(m.close & (m.method == "star"), "ph", "won", "CLOSE games, star tie-break decided")
    ml(m.index == m.index, "mside", "mwon", "Ratings margin side, all")
    for lo, hi in ((.5, .6), (.6, .7), (.7, .8), (.8, 1.01)):
        ml((m.conf >= lo) & (m.conf < hi), "mside", "mwon", f"  ratings conf {lo:.0%}-{hi:.0%}")
    dog = np.where(m.ph, m[f"{book}_hml"], m[f"{book}_aml"]) > 0
    ml(dog, "ph", "won", "H2H pick is a Bovada/market underdog")
    # ATS of the H2H pick at that book's spread
    sp = df.dropna(subset=[f"{book}_spread"]).copy()
    hm = sp.hp - sp.ap + sp[f"{book}_spread"]; sp = sp[hm != 0]; hm = hm[hm != 0]
    cov = np.where(sp.ph, hm > 0, hm < 0)
    pr = np.where(sp.ph, sp[f"{book}_hso"], sp[f"{book}_aso"]); pr = np.where(np.isnan(pr), -110, pr)
    prof = np.where(cov, pay(pr), -1.0); sp = sp.assign(profit=prof, cov=cov)
    P(f"  ATS H2H pick: n={len(sp)} cover {cov.mean():.1%} ROI {prof.mean():+.1%} | {season_str(sp, 'profit')}")
    msk = sp.close.to_numpy()
    P(f"  ATS close games: n={msk.sum()} cover {cov[msk].mean():.1%} ROI {prof[msk].mean():+.1%}")
    # O/U
    o = df.dropna(subset=[f"{book}_total"]).copy(); act = o.hp + o.ap; o = o[act != o[f"{book}_total"]]
    over = (o.hp + o.ap > o[f"{book}_total"]).to_numpy()
    oo = o[f"{book}_oo"].fillna(-110).to_numpy(); uo = o[f"{book}_uo"].fillna(-110).to_numpy()
    def ou(mask, side_over, label):
        mask = np.asarray(mask); so = np.asarray(side_over)[mask] if np.ndim(side_over) else np.full(mask.sum(), side_over)
        w = np.where(so, over[mask], ~over[mask]); pr = np.where(so, oo[mask], uo[mask])
        prof = np.where(w, pay(pr), -1.0); s = o[mask].assign(profit=prof)
        P(f"  {label}: n={mask.sum()} hit {w.mean() if len(w) else float('nan'):.1%} ROI {prof.mean() if len(w) else float('nan'):+.1%} | {season_str(s, 'profit')}")
    bo = ((o.h_over3 >= 2) & (o.a_over3 >= 2)).to_numpy(); bu = ((o.h_over3 <= 1) & (o.a_over3 <= 1)).to_numpy()
    ro = (o.total > o[f"{book}_total"]).to_numpy()
    ou(np.ones(len(o), bool), ro, "O/U rating-total side, all")
    ou(np.abs(o.total - o[f"{book}_total"]).to_numpy() >= 3, ro, "O/U rating total 3+ off line")
    ou(bo, True, "Over trend -> FOLLOW (OVER)"); ou(bo, False, "Over trend -> FADE (UNDER)")
    ou(bu, False, "Under trend -> FOLLOW (UNDER)"); ou(bu, True, "Under trend -> FADE (OVER)")
    ou(bo & ~ro, False, "Over-fade + rating agrees UNDER"); ou(bu & ro, True, "Under-fade + rating agrees OVER")
    P()
print("\n".join(L))
