"""Script totals (O/U) divergence-tier backtest. PAPER ONLY, not wired live.
Walk-forward: each game's projection uses only games strictly before its date.
Marv proj total = season-to-date opponent-adjusted scoring ratings (raw, unshrunk) -> divergence d = proj - bet line.
EV: 5,000-iter MC on 50/50 market-shrunk mean (+ TCI/PPE pace logit tweak K*sP), side = direction of d, need EV>0.
Bet line = OPEN where available (CFB cfb_lines_oc, WNBA wnba_open_lines, soccer opening avg O/U2.5 odds); CLV vs close.
NFL: nflverse close line + real O/U odds -> CLV n/a. CFB/WNBA: no price history -> -110 assumed.
Usage: nice python bt_totals.py   (writes bets_totals.csv, report.json)"""
import sys, json, glob, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/opt/marv-bot"); sys.path.insert(0, "/opt/marv-bot/experiments/tci_ppe_uf")
import bt as B
from scipy.stats import poisson
from scipy.optimize import brentq
C = B.C; N = 5000; K = 0.10; rng = np.random.default_rng(11)
START, T0, T1 = pd.Timestamp("2025-05-01"), pd.Timestamp("2026-09-21"), pd.Timestamp("2026-10-13")

def be(a): return B.be(a)
def pay(a): return B.payout(a)

def load(name):
    df, R, P = getattr(B, name)()
    df = df.copy(); df["gid"] = df.gid.astype(str); df["close"] = df.total; df["open"] = np.nan
    if "op" not in df: df["op"] = -110.0; df["up"] = -110.0
    if name == "cfb":
        o = pd.read_csv(C+"cfb_lines_oc.csv", dtype={"game_id": str}).drop_duplicates("game_id")
        df = df.merge(o[["game_id", "total_open", "total"]].rename(columns={"game_id": "gid", "total": "tc"}), on="gid", how="left")
        df["open"] = df.total_open; df["close"] = df.tc.fillna(df.close)
    if name == "wnba":
        o = pd.read_csv(C+"wnba_open_lines.csv", dtype={"game_id": str}).drop_duplicates("game_id")
        df = df.merge(o[["game_id", "t_open", "t_close"]].rename(columns={"game_id": "gid"}), on="gid", how="left")
        df["open"] = df.t_open; df["close"] = df.t_close.fillna(df.close)
    if name == "soccer":  # fixed 2.5 line; market-implied goals from de-vigged open odds; closing odds for CLV
        from marv.soccer_max.data import _normalize
        fr = []
        for f in sorted(glob.glob(C+"soccer_fd/*_2526.csv")+glob.glob(C+"soccer_fd/*_2627.csv")):
            raw = pd.read_csv(f, on_bad_lines="skip"); raw.columns = [str(c).replace("\ufeff", "").replace("ï»¿", "").strip() for c in raw.columns]
            fr.append(_normalize(raw.copy(), f.split("/")[-1].split("_")[0], 0))
        g = pd.concat(fr).dropna(subset=["o_h", "o_a", "o_d"]).reset_index(drop=True)
        df["co"] = g.get("c_over").values; df["cu"] = g.get("c_under").values; df["oo"] = g.o_over.values; df["ou"] = g.o_under.values
    return df, R, P

def lam_from(po):  # Poisson total-goals mean giving P(T>=3)=po
    try: return brentq(lambda l: 1-poisson.cdf(2, l)-po, 0.2, 8)
    except Exception: return np.nan

def run(name):
    df, R, P = load(name); soc = bool(P.get("soccer"))
    fin = df.dropna(subset=["hs", "as_"]).sort_values("date").reset_index(drop=True)
    Rk = R.set_index(["gid", "team"])[["ppe"]]; Rk = Rk[~Rk.index.duplicated()]
    out = []
    for _, x in fin[(fin.date >= START) & (fin.date < T1)].iterrows():
        past = fin[(fin.date < x.date) & (fin.date >= x.date-pd.Timedelta(days=400))]
        def rate(t):
            a = past[past.home == t]; b = past[past.away == t]; n = len(a)+len(b)
            return None if n < 3 else ((a.hs.sum()+b.as_.sum())/n, (a.as_.sum()+b.hs.sum())/n)
        rh, ra = rate(x.home), rate(x.away)
        if not rh or not ra: continue
        Lg = (past.hs.mean()+past.as_.mean())/2; hfa = 0 if x.neutral else P["hfa"]
        proj = (rh[0]+ra[1]-Lg+hfa/2) + (ra[0]+rh[1]-Lg-hfa/2)
        sP = sum(float(Rk.loc[(x.gid, t)].ppe) if (x.gid, t) in Rk.index else 0.0 for t in (x.home, x.away))
        res_tot = x.hs+x.as_
        if soc:
            if pd.isna(x.oo) or pd.isna(x.ou) or x.oo <= 1 or x.ou <= 1: continue
            po = (1/x.oo)/((1/x.oo)+(1/x.ou)); mline = lam_from(po)
            if np.isnan(mline): continue
            line = 2.5; d = proj-mline
            sim = rng.poisson(max(0.3, 0.5*proj+0.5*mline), N); pov = (sim >= 3).mean()
            pr_o, pr_u = B.dec2am(x.oo), B.dec2am(x.ou)
        else:
            line = x.open if pd.notna(x.open) else x.close
            if pd.isna(line): continue
            d = proj-line; sim = rng.normal(0.5*proj+0.5*line, P["sd_t"], N)
            w, l = (sim > line).mean(), (sim < line).mean(); pov = w/(w+l)
            pr_o = -110 if pd.isna(x.op) else x.op; pr_u = -110 if pd.isna(x.up) else x.up
        pov = B.sig(B.logit(pov)+K*sP)
        side = 1 if d > 0 else -1; p = pov if side > 0 else 1-pov; price = float(pr_o if side > 0 else pr_u)
        ev = p*pay(price)-(1-p)
        r = np.sign(res_tot-line)*side; u = pay(price) if r > 0 else (-1.0 if r < 0 else 0.0)
        # CLV
        if soc:
            if pd.notna(x.co) and pd.notna(x.cu) and x.co > 1 and x.cu > 1:
                pc = (1/x.co)/((1/x.co)+(1/x.cu)); clv = (pc-po)*side*100  # pp of no-vig prob moved our way
            else: clv = np.nan
        else:
            clv = (x.close-line)*side if (pd.notna(x.open) and pd.notna(x.close)) else np.nan
        # UF (same rules as tci_ppe_uf): rivalry chaos / emotional (blowout loss last game, short rest) / injury cluster
        def lastm(t):
            a = past[(past.home == t) | (past.away == t)].tail(1)
            if a.empty: return 0
            q = a.iloc[0]; return (q.hs-q.as_) if q.home == t else (q.as_-q.hs)
        emo = min(lastm(x.home), lastm(x.away)) <= -P["blow"] or bool(x.short_rest)
        uf = 0 if (x.rival or emo or x.inj) else 1
        out.append(dict(sport=name, date=x.date, game=f"{x.away}@{x.home}", line=line, proj=round(proj, 2), d=round(d, 2),
                        side=side, price=price, p=round(p, 3), ev=round(ev, 3), uf=uf, units=u, clv=clv))
    return pd.DataFrame(out)

def summ(b):
    if len(b) == 0: return dict(n=0)
    b = b.sort_values("date"); cu = b.units.cumsum().values
    dd = float((np.maximum.accumulate(np.r_[0, cu])[1:]-cu).max())
    return dict(n=len(b), W=int((b.units > 0).sum()), L=int((b.units < 0).sum()), P=int((b.units == 0).sum()),
                u=round(b.units.sum(), 2), roi=round(b.units.sum()/len(b), 3), clv=round(b.clv.mean(), 2) if b.clv.notna().any() else None,
                clv_n=int(b.clv.notna().sum()), mdd=round(dd, 2))

PDF = dict(nfl=(1.5, 3), cfb=(1.5, 3), wnba=(2, 4), soccer=(0.3, 0.6))  # (lean, extreme) |d|
def tiers(b, sp):
    lean, ext = PDF[sp]; a = b.d.abs()
    return dict(all=b, lean_ext=b[a >= lean], extreme=b[a >= ext], lean_only=b[(a >= lean) & (a < ext)])

if __name__ == "__main__":
    allb = []
    for sp in ("nfl", "cfb", "wnba", "soccer"):
        try: b = run(sp); allb.append(b); print(sp, len(b), flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
    Bt = pd.concat(allb); Bt.to_csv("bets_totals.csv", index=False)
    rep = {}
    for sp in Bt.sport.unique():
        S = Bt[Bt.sport == sp]
        for win, W in (("early", S[S.date < T0]), ("recent", S[S.date >= T0])):
            for tn, T in tiers(W, sp).items():
                for ufm, U in (("noUF", T), ("UF1", T[T.uf == 1])):
                    rep[f"{sp}|{win}|{tn}|{ufm}|EV>0"] = summ(U[U.ev > 0])
                    if tn == "all": rep[f"{sp}|{win}|all|{ufm}|noEV"] = summ(U)
        # optimize threshold on EARLY only (min 40 bets, maximize ROI with a shrink penalty), validate on recent
        E, Rr = S[S.date < T0], S[S.date >= T0]
        step = 0.1 if sp == "soccer" else 0.5; grid = np.arange(0, (1.5 if sp == "soccer" else 10)+1e-9, step)
        best = None; cand = []
        for th in grid:
            for ufr in (0, 1):
                for evr in (0, 1):
                    m = E[(E.d.abs() >= th) & ((E.uf == 1) | (ufr == 0)) & ((E.ev > 0) | (evr == 0))]
                    if len(m) < 40: continue
                    sc = m.units.sum()/len(m) - 1/np.sqrt(len(m))  # conservative: ROI minus ~1 SE
                    cand.append((sc, th, ufr, evr, len(m)))
        if cand:
            sc, th, ufr, evr, n = max(cand)
            sel = lambda X: X[(X.d.abs() >= th) & ((X.uf == 1) | (ufr == 0)) & ((X.ev > 0) | (evr == 0))]
            rep[f"{sp}|OPT"] = dict(th=th, UF=ufr, EV=evr, train=summ(sel(E)), holdout=summ(sel(Rr)))
        # direction split for extreme tier, early+recent
        for win, W in (("early", E), ("recent", Rr)):
            X = W[(W.d.abs() >= PDF[sp][1]) & (W.ev > 0)]
            rep[f"{sp}|{win}|extreme|over"] = summ(X[X.side > 0]); rep[f"{sp}|{win}|extreme|under"] = summ(X[X.side < 0])
    json.dump(rep, open("report.json", "w"), indent=0, default=str)
    for k, v in rep.items(): print(k, json.dumps(v, default=str))
