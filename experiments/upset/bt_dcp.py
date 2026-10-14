"""(A) Dynamic Confidence Penalty on favourites + (B) UVF dog travel/trench gates. NFL 2020-26, pre-game data only."""
import numpy as np, pandas as pd, json, warnings; warnings.filterwarnings("ignore")
X = "/opt/marv-bot/experiments/upset/"; C = "/opt/marv-bot/state/cache/"
def pay(a): a = np.asarray(a, float); return np.where(a > 0, a/100, 100/np.abs(a))
def be(a): a = np.asarray(a, float); return np.where(a > 0, 100/(a+100), np.abs(a)/(np.abs(a)+100))
P = pd.read_csv(X+"dog_probs.csv", parse_dates=["date"]); P = P[P.sport == "nfl"]
src = open(X+"bt_upset.py").read().split("D=tags_nfl(nfl())")[0]; ns = {}; exec(src, ns)
U = ns["tags_nfl"](ns["nfl"]())
g = pd.read_csv(C+"nflverse_games.csv"); g = g[g.season >= 2020]
TZ = {"ARI":-7,"ATL":-5,"BAL":-5,"BUF":-5,"CAR":-5,"CHI":-6,"CIN":-5,"CLE":-5,"DAL":-6,"DEN":-7,"DET":-5,"GB":-6,"HOU":-6,"IND":-5,"JAX":-5,
      "KC":-6,"LA":-8,"LAC":-8,"LV":-8,"MIA":-5,"MIN":-6,"NE":-5,"NO":-6,"NYG":-5,"NYJ":-5,"PHI":-5,"PIT":-5,"SEA":-8,"SF":-8,"TB":-5,"TEN":-6,"WAS":-5}
gi = g.set_index("game_id")
def side(r, team, col): x = gi.loc[r.gid]; return x["home_"+col] if x.home_team == team else x["away_"+col]
F = U[["gid","team","t_rz3","t_rz_s","pace_p","var_p","t_dsack_p","o_dsack_p"]]
P = P.merge(F.rename(columns={"team":"fav"}), on=["gid","fav"], how="left")
P = P.merge(U[["gid","team","t_dsack_p","o_dsack_p"]].rename(columns={"team":"dog","t_dsack_p":"dog_dsack_p","o_dsack_p":"fav_dsack_p2"}), on=["gid","dog"], how="left")
P["fav_rest"] = [side(r, r.fav, "rest") for r in P.itertuples()]; P["dog_rest"] = [side(r, r.dog, "rest") for r in P.itertuples()]
P["loc"] = [gi.loc[r.gid].location for r in P.itertuples()]; P["gtime"] = [gi.loc[r.gid].gametime for r in P.itertuples()]
P["home"] = [gi.loc[r.gid].home_team for r in P.itertuples()]; P["wk"] = [gi.loc[r.gid].week for r in P.itertuples()]
# markers on the favourite
P["m_rz"] = (P.t_rz3 <= P.t_rz_s - 0.10).fillna(False)
P["m_pace"] = ((P.pace_p <= 0.25) | (P.var_p >= 0.75)).fillna(False)
P["m_rest"] = (P.fav_rest < P.dog_rest).fillna(False)
P["nmark"] = P[["m_rz","m_pace","m_rest"]].sum(axis=1)
# UVF (B) dog flags
P["b_trench"] = ((P.dog_dsack_p >= 0.8) & (P.fav_dsack_p2 <= 0.3)).fillna(False)   # dog pass rush top-20% vs fav's pass rush bottom-30%
P["b_intl"] = P["loc"].eq("Neutral") & ~P.wk.between(19, 30)
tzshift = np.array([abs(TZ.get(r.fav, -5) - TZ.get(r.home, -5)) if r.fav != r.home else 0 for r in P.itertuples()])
P["b_travel"] = (tzshift >= 2) | P.b_intl
P["b_bodyclock"] = np.array([(r.fav != r.home) and TZ.get(r.fav, -5) == -8 and TZ.get(r.home, -5) == -5 for r in P.itertuples()]) & (pd.to_numeric(P.gtime.astype(str).str[:2], errors="coerce") <= 13)
P["b_rest"] = (P.fav_rest <= P.dog_rest - 3) | ((P.fav_rest <= 4) & (P.dog_rest >= 6))
P["b_any"] = P[["b_trench","b_intl","b_travel","b_bodyclock","b_rest"]].any(axis=1)
P["pfav"] = 1 - P.p_model; P["pmk_fav"] = 1 - P.p_mkt; P["fav_ml"] = P.oml; P["fav_won"] = 1 - P.won
W = {"A_aug1_oct12_26": (P.date >= "2026-08-01") & (P.date <= "2026-10-12"), "B_sep21_oct12_26": (P.date >= "2026-09-21") & (P.date <= "2026-10-12"),
     "prior_2020_24": P.season <= 2024, "oos_2025_26": P.season >= 2025}
def dd(u): c = np.cumsum(u); return float((np.maximum.accumulate(np.r_[0, c])[1:] - c).max()) if len(u) else 0.0
L = []; res = {}
# ---------------- (A) favourite ML picks (Master spec: edge = p - break-even >= 3%)
pens = {"none": lambda p, r: p, "shrink15_to_mkt": lambda p, r: p - 0.15*(p - r.pmk_fav), "edge_x0.85": lambda p, r: be(r.fav_ml) + 0.85*(p - be(r.fav_ml)),
        "shrink15_to_50": lambda p, r: p - 0.15*(p - 0.5)}
for need_m, mk in (("2+", P.nmark >= 2), ("1+", P.nmark >= 1)):
    for nm, f in pens.items():
        for uf in (False, True):
            p = np.where(mk, f(P.pfav, P), P.pfav) if nm != "none" else P.pfav
            pick = (p - be(P.fav_ml) >= 0.03) & ((~P.chaos) if uf else True)
            base = (P.pfav - be(P.fav_ml) >= 0.03) & ((~P.chaos) if uf else True)
            for w, wm in W.items():
                s = P[pick & wm].sort_values("date"); u = np.where(s.fav_won == 1, pay(s.fav_ml), -1.0)
                drop = P[base & ~pick & wm]
                k = f"A markers{need_m} {nm} {'UFv2' if uf else 'noUF'} {w}"
                res[k] = dict(n=len(s), units=round(u.sum(), 2), roi=round(u.mean(), 3) if len(u) else None, maxdd=round(dd(u), 2),
                              dropped=len(drop), winners_lost=int(drop.fav_won.sum()), losers_avoided=int((1 - drop.fav_won).sum()),
                              dropped_units=round(float(np.where(drop.fav_won == 1, pay(drop.fav_ml), -1.0).sum()), 2))
                if nm in ("none", "shrink15_to_mkt", "edge_x0.85", "shrink15_to_50"): L.append(k.ljust(52) + json.dumps(res[k]))
# marker-count diagnostic: base fav picks by number of markers
for w, wm in W.items():
    b = P[(P.pfav - be(P.fav_ml) >= 0.03) & wm]
    for m in (0, 1, 2, 3):
        s = b[b.nmark == m]; u = np.where(s.fav_won == 1, pay(s.fav_ml), -1.0)
        L.append(f"A diag {w} markers={m}: n={len(s)} roi={u.mean() if len(u) else float('nan'):+.3f} win={s.fav_won.mean() if len(s) else float('nan'):.3f}")
# parlay demotion: each week pair the 2 best-edge fav ML picks (2-leg); demoted (2+ markers) legs excluded
def parl(excl, wm):
    b = P[(P.pfav - be(P.fav_ml) >= 0.03) & ~P.chaos & wm].copy(); b["e"] = b.pfav - be(b.fav_ml)
    if excl: b = b[b.nmark < 2]
    u = []
    for (_, _), x in b.sort_values("e", ascending=False).groupby(["season", "wk"]):
        if len(x) < 2: continue
        a, c = x.iloc[0], x.iloc[1]; dec = (1 + pay(a.fav_ml)) * (1 + pay(c.fav_ml))
        u.append(dec - 1 if a.fav_won and c.fav_won else -1.0)
    u = np.array(u); return dict(n=len(u), units=round(u.sum(), 2), roi=round(u.mean(), 3) if len(u) else None, maxdd=round(dd(u), 2))
for w, wm in W.items():
    for ex in (False, True):
        res[f"A parlay {'demote2+' if ex else 'base'} {w}"] = parl(ex, wm); L.append(f"A parlay {'demote2+' if ex else 'base'} {w} " + json.dumps(res[f'A parlay {"demote2+" if ex else "base"} {w}']))
# ---------------- (B) dog flags, ML >= +120
for fl in ("b_trench", "b_intl", "b_travel", "b_bodyclock", "b_rest", "b_any"):
    for edge in (False, True):
        m = (P.ml >= 120) & P[fl] & ((P.edge >= 0) if edge else True)
        for w, wm in W.items():
            s = P[m & wm]; u = np.where(s.won == 1, pay(s.ml), -1.0)
            k = f"B {fl}{' +edge>=0' if edge else ''} {w}"
            res[k] = dict(n=len(s), win=round(s.won.mean(), 3) if len(s) else None, imp=round(s.p_mkt.mean(), 3) if len(s) else None, roi=round(u.mean(), 3) if len(u) else None)
            L.append(k.ljust(44) + json.dumps(res[k]))
# ---------------- cited games
cite = [("2026_04", "BUF", "NE"), ("2026_04", "PHI", "LA"), ("2026_05", "PHI", "JAX"), ("2026_05", "CLE", "NYJ"), ("2026_02", "PHI", "TEN")]
for wk, a, b in cite:
    s = P[P.gid.str.startswith(wk) & P.gid.str.contains(a) & P.gid.str.contains(b)]
    if s.empty: L.append(f"CITE {wk} {a}-{b}: not in data (no ML/needs 3 prior games)"); continue
    r = s.iloc[0]
    L.append(f"CITE {r.gid}: fav {r.fav} {r.fav_ml:+.0f} dog {r.dog} {r.ml:+.0f} dog_won={int(r.won)} | markers rz={r.m_rz} pace={r.m_pace} rest={r.m_rest} (n={r.nmark}) "
             f"| UVF trench={r.b_trench} intl={r.b_intl} travel={r.b_travel} bodyclock={r.b_bodyclock} rest={r.b_rest} | model dog edge={r.edge:+.3f} chaos={r.chaos}")
json.dump(res, open(X+"summary_dcp.json", "w"), indent=1, default=float)
print("\n".join(L))
