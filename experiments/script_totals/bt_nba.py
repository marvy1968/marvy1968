"""NBA pregame totals: same divergence-tier backtest as bt_totals.py. Walk-forward (only games before each date).
Data: state/cache/nba_odds.sqlite (OU line, assumed -110; single line -> no CLV). Train 2023-24 + 2024-25, hold out 2025-26."""
import sqlite3, json, numpy as np, pandas as pd
from math import erf, sqrt
con = sqlite3.connect("/opt/marv-bot/state/cache/nba_odds.sqlite")
g = pd.concat([pd.read_sql(f'select * from "{t}"', con).assign(season=s) for t, s in (("2023-24", 2024), ("2024-25", 2025), ("odds_2025-26", 2026))])
g["date"] = pd.to_datetime(g.Date); g = g.dropna(subset=["OU", "Points"]).sort_values("date").reset_index(drop=True)
g["hs"] = (g.Points + g.Win_Margin) / 2; g["as_"] = (g.Points - g.Win_Margin) / 2   # Win_Margin = home - away
SD, PAY = 19.0, 100/110; rows = []
for i, x in g.iterrows():
    past = g[(g.date < x.date) & (g.date >= x.date - pd.Timedelta(days=200))]
    def rate(t):
        a = past[past.Home == t]; b = past[past.Away == t]; n = len(a)+len(b)
        return None if n < 5 else ((a.hs.sum()+b.as_.sum())/n, (a.as_.sum()+b.hs.sum())/n)
    rh, ra = rate(x.Home), rate(x.Away)
    if not rh or not ra: continue
    Lg = (past.hs.mean()+past.as_.mean())/2
    proj = (rh[0]+ra[1]-Lg+1) + (ra[0]+rh[1]-Lg-1); d = proj - x.OU; side = 1 if d > 0 else -1
    mu = 0.5*proj+0.5*x.OU; po = 1-0.5*(1+erf((x.OU-mu)/SD/sqrt(2))); p = po if side > 0 else 1-po
    def lastm(t):
        a = past[(past.Home == t) | (past.Away == t)].tail(1)
        if a.empty: return 0
        q = a.iloc[0]; return q.Win_Margin if q.Home == t else -q.Win_Margin
    uf = 0 if min(lastm(x.Home), lastm(x.Away)) <= -20 else 1
    r = np.sign(x.Points - x.OU)*side
    rows.append(dict(season=x.season, date=x.date, d=d, side=side, ev=p*PAY-(1-p), uf=uf, units=PAY if r > 0 else (-1.0 if r < 0 else 0.0)))
B = pd.DataFrame(rows); B.to_csv("bets_nba.csv", index=False)
def summ(b):
    if len(b) == 0: return dict(n=0)
    cu = b.units.cumsum().values; dd = float((np.maximum.accumulate(np.r_[0, cu])[1:]-cu).max())
    return dict(n=len(b), W=int((b.units > 0).sum()), L=int((b.units < 0).sum()), u=round(b.units.sum(), 2), roi=round(b.units.mean(), 3), mdd=round(dd, 1))
E, H = B[B.season < 2026], B[B.season == 2026]; rep = {}
for nm, W in (("train", E), ("holdout", H)):
    a = W.d.abs(); ev = W.ev > 0
    for tn, m in (("all", a >= 0), ("lean2-4", (a >= 2) & (a < 4)), ("ext>=4", a >= 4), ("ext>=5", a >= 5)):
        rep[f"{nm}|{tn}|EV"] = summ(W[m & ev]); rep[f"{nm}|{tn}|EV+UF"] = summ(W[m & ev & (W.uf == 1)])
    rep[f"{nm}|ext>=4|over"] = summ(W[(a >= 4) & ev & (W.side > 0)]); rep[f"{nm}|ext>=4|under"] = summ(W[(a >= 4) & ev & (W.side < 0)])
cand = []
for th in np.arange(2, 12.01, 0.5):
    for ufr in (0, 1):
        m = E[(E.d.abs() >= th) & (E.ev > 0) & ((E.uf == 1) | (ufr == 0))]
        if len(m) >= 60: cand.append((m.units.mean()-1/np.sqrt(len(m)), th, ufr))
_, th, ufr = max(cand); sel = lambda X: X[(X.d.abs() >= th) & (X.ev > 0) & ((X.uf == 1) | (ufr == 0))]
rep["OPT"] = dict(th=th, UF=ufr, train=summ(sel(E)), holdout=summ(sel(H)))
json.dump(rep, open("report_nba.json", "w"), indent=0, default=str)
for k, v in rep.items(): print(k, json.dumps(v, default=str))
