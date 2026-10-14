import numpy as np, pandas as pd, json, warnings; warnings.filterwarnings("ignore")
from scipy.optimize import minimize
D = pd.read_csv("/opt/marv-bot/experiments/upset/dog_probs.csv", parse_dates=["date"])
def pay(a): a = np.asarray(a, float); return np.where(a > 0, a/100, 100/np.abs(a))
D["pnl"] = np.where(D.won == 1, pay(D.ml), -1.0)
# NFL per-play efficiency tag (bt_upset.py definitions)
src = open("/opt/marv-bot/experiments/upset/bt_upset.py").read().split("D=tags_nfl(nfl())")[0]; ns = {}; exec(src, ns)
T = ns["tags_nfl"](ns["nfl"]())[["gid", "team", "uvf_eff"]].rename(columns={"team": "dog"})
D = D.merge(T, on=["gid", "dog"], how="left"); D["uvf_eff"] = D.uvf_eff.fillna(False).astype(bool)
D["rest_edge"] = (D.sport == "wnba") & (D.d_rest >= 1)
W = {"A_aug1_oct12_26": (D.date >= "2026-08-01") & (D.date <= "2026-10-12"), "B_sep21_oct12_26": (D.date >= "2026-09-21") & (D.date <= "2026-10-12"),
     "prior_<=2025": D.date < "2026-01-01", "oos_2025_26": D.season >= 2025}
def S(x): return dict(n=len(x), w=round(x.won.mean(), 3) if len(x) else None, imp=round(x.p_mkt.mean(), 3) if len(x) else None,
                      roi=round(x.pnl.mean(), 3) if len(x) else None)
PB = {"+120+": D.ml >= 120, "+120-149": (D.ml >= 120) & (D.ml < 150), "+150-199": (D.ml >= 150) & (D.ml < 200), "+200+": D.ml >= 200}
EB = {"<0": D.edge < 0, "0-3": (D.edge >= 0) & (D.edge < .03), "3-6": (D.edge >= .03) & (D.edge < .06), "6-10": (D.edge >= .06) & (D.edge < .10), "10+": D.edge >= .10}
res = {}; L = []
def row(key, m):
    res[key] = {w: S(D[m & wm]) for w, wm in W.items()}
    L.append(key.ljust(44) + " | ".join(f"{w[:6]} n={s['n']} w={s['w']} imp={s['imp']} roi={s['roi']}" for w, s in res[key].items()))
for sp in ("nfl", "wnba"):
    for pn, pm in PB.items():
        for en, em in EB.items():
            row(f"1 {sp} {pn} edge {en}", (D.sport == sp) & pm & em)
    row(f"1 {sp} +120+ edge3+ non-chaos", (D.sport == sp) & PB["+120+"] & (D.edge >= .03) & ~D.chaos)
    row(f"1 {sp} +120+ edge3+ chaos", (D.sport == sp) & PB["+120+"] & (D.edge >= .03) & D.chaos)
    row(f"1 {sp} +120+ edge10+ chaos", (D.sport == sp) & PB["+120+"] & (D.edge >= .10) & D.chaos)
for tag in ("uvf_eff", "rest_edge"):
    sp = "nfl" if tag == "uvf_eff" else "wnba"
    for en, em in (("edge>=0", D.edge >= 0), ("edge>=3", D.edge >= .03), ("edge 3-10", (D.edge >= .03) & (D.edge < .10))):
        row(f"2 {sp} {tag} + {en} +120+", (D.sport == sp) & PB["+120+"] & D[tag] & em)
# (3) logistic upset model: train season<=2024, test 2025-26
F = ["lp", "dspread", "d_tci", "d_ppe", "d_tf", "d_last", "d_rest", "dog_home"]
D["lp"] = np.log(D.p_mkt/(1-D.p_mkt)); D["d_rest"] = D.d_rest.clip(-7, 7)
for sp in ("nfl", "wnba"):
    tr = D[(D.sport == sp) & (D.season <= 2024)]; X = tr[F].values; mu, sd = X.mean(0), X.std(0)+1e-9
    Xs = np.c_[np.ones(len(X)), (X-mu)/sd]; y = tr.won.values
    nll = lambda b: np.sum(np.logaddexp(0, Xs@b)) - y@(Xs@b) + 1.0*np.sum(b[1:]**2)
    b = minimize(nll, np.zeros(Xs.shape[1]), method="BFGS").x
    m = D.sport == sp; Xa = np.c_[np.ones(m.sum()), (D.loc[m, F].values-mu)/sd]
    D.loc[m, "p_lr"] = 1/(1+np.exp(-(Xa@b)))
    te = D[m & (D.season >= 2025)]
    ll = lambda p: -np.mean(te.won*np.log(p)+(1-te.won)*np.log(1-p))
    L.append(f"3 {sp} logistic coefs " + ", ".join(f"{k}={v:+.2f}" for k, v in zip(["c"]+F, b)) +
             f" | OOS logloss model={ll(te.p_lr):.4f} market={ll(te.p_mkt):.4f} n={len(te)}")
    res[f"3 {sp} coefs"] = dict(zip(["c"]+F, map(float, b)))
D["lr_edge"] = D.p_lr - D.p_mkt
for sp in ("nfl", "wnba"):
    for th in (0.0, 0.03, 0.05):
        row(f"3 {sp} logit edge>={th} +120+", (D.sport == sp) & PB["+120+"] & (D.lr_edge >= th) & (D.season >= 2025))
json.dump(res, open("/opt/marv-bot/experiments/upset/summary_dog.json", "w"), indent=1, default=float)
print("\n".join(L))
