"""Prop grade v2 backtest: fit on older games (2025 wk1-6), test on unseen (2025 wk7-10 all real lines; Oct 2026 March_edge picks)."""
import json, sys, numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
OUT = "/tmp/pg"; TH = 3.0; PROP_CAP = 0.03
def payout(a): a = np.asarray(a, float); return np.where(a > 0, a / 100, 100 / -a)
def lg(p): p = np.clip(np.asarray(p, float), 0.02, 0.98); return np.log(p / (1 - p))
def clip(x, a=1.0): return np.clip(x, -a, a)

FEATS = ["f_model", "f_form", "f_def", "f_usage", "f_team", "under", "f_mkt"]
def prep(d):
    d = d.copy(); s = np.where(d.side == "over", 1.0, -1.0); d["sgn"] = s
    pm = np.where(s > 0, d.pm_over, 1 - d.pm_over)
    pf = np.where(s > 0, d.pf_over, 1 - d.pf_over); pf = np.where(np.isnan(pf), pm, pf)
    nv = d.nv_side.astype(float); d["f_mkt"] = lg(nv)
    d["f_model"] = clip(lg(pm) - lg(nv), 1.5); d["f_form"] = clip(lg(pf) - lg(nv), 1.5)
    d["f_def"] = s * clip(d.def_ratio.fillna(1.0) - 1.0, 0.5)
    d["f_usage"] = s * clip(d.usage_trend.fillna(0.0), 0.5)
    d["f_team"] = s * clip((d.implied_pts.fillna(22.0) - 22.0) / 7.0, 1.5)        # market pace/script: team implied points
    d["under"] = (s < 0).astype(float)
    dd = (d.tot_hat - d.total_line)                                                 # Marv projected total - market total
    d["script_d"] = dd
    d["script_grade"] = np.select([dd >= TH, dd >= TH / 2, dd <= -TH, dd <= -TH / 2], ["High Shootout", "Lean Shootout", "Def Grind", "Lean Grind"], "Neutral")
    d.loc[dd.isna(), "script_grade"] = "n/a"
    d["script_shift"] = (s * PROP_CAP * 0.6 * clip(dd.fillna(0.0) / TH)).astype(float)  # same formula/cap as game_script.read (pace term 0: not logged historically)
    d["pay"] = payout(d.price)
    return d

def fit(tr, C=0.3):
    m = LogisticRegression(C=C, max_iter=2000); m.fit(tr[FEATS], tr.won.astype(int)); return m

def grade(m, d, t_up, t_dn, script=True, cap2=True):
    p = m.predict_proba(d[FEATS])[:, 1] + (d.script_shift if script else 0.0)
    p = np.clip(p, 0.01, 0.99)
    ev = p * (1 + d.pay) - 1
    g = np.where(ev >= t_up, "up", np.where(ev <= t_dn, "down", "neutral"))
    # a thumbs up also needs player form or Marv's projection on its side (not just the market/under prior)
    g = np.where((g == "up") & (d.f_form <= 0) & (d.f_model <= 0), "neutral", g)
    d = d.assign(p_grade=p, ev_grade=ev, grade=g)
    if cap2:   # same-game cap: at most 2 over thumbs-ups per game (best EV kept), so props don't stack the total
        key = d.get("game_id")
        if key is not None:
            o = d[(d.grade == "up") & (d.side == "over")].sort_values("ev_grade", ascending=False)
            drop = o.groupby("game_id").cumcount() >= 2
            d.loc[o.index[drop.values], "grade"] = "neutral"
    return d

def res(d, name):
    d = d[~d.push.astype(bool)] if "push" in d else d
    n = len(d); w = int(d.won.sum()); pnl = np.where(d.won, d.pay, -1.0).sum()
    return {"set": name, "n": n, "W": w, "L": n - w, "win%": round(100 * w / n, 1) if n else None, "ROI%": round(100 * pnl / n, 1) if n else None}

A = prep(pd.read_csv(f"{OUT}/A.csv")); A = A[~A.push.astype(bool)]
tune, hold = A[A.week <= 6], A[A.week >= 7]
print("tune", len(tune), tune.date.min(), tune.date.max(), "| holdout", len(hold), hold.date.min(), hold.date.max())

# ---- choose threshold inside the tune period only: fit wk1-4, check wk5-6 ----
inner_tr, inner_va = tune[tune.week <= 4], tune[tune.week >= 5]
mi = fit(inner_tr); rows = []
for t in (0.0, 0.02, 0.03, 0.05, 0.08):
    for sc in (False, True):
        gi = grade(mi, inner_va, t, -t if t > 0 else -0.03, script=sc)
        rows.append(dict(res(gi[gi.grade == "up"], f"inner wk5-6 up EV>={t:.0%} script={sc}")))
print(pd.DataFrame(rows).to_string(index=False))
T_UP = float(sys.argv[1]) if len(sys.argv) > 1 else 0.03
m = fit(tune)
print("coef", dict(zip(FEATS, np.round(m.coef_[0], 3))), "intercept", round(m.intercept_[0], 3))
json.dump({"feats": FEATS, "coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0]), "t_up": T_UP, "t_dn": -T_UP,
           "fit": "2025 NFL weeks 1-6 real lines (props_backtest_real), both sides, pushes dropped", "n_fit": int(len(tune))},
          open(f"{OUT}/model.json", "w"), indent=1)
H = grade(m, hold, T_UP, -T_UP)
out = [res(hold, "holdout: every side (no filter)"), res(hold[hold.side == "under"], "holdout: always under"),
       res(hold[(hold.f_model > 0) & (lg(np.where(hold.sgn > 0, hold.pm_over, 1 - hold.pm_over)) > 0)], "holdout: old Marv model edge>0"),
       res(H[H.grade == "up"], "holdout: grade 👍"), res(H[H.grade == "neutral"], "holdout: grade neutral"), res(H[H.grade == "down"], "holdout: grade 👎")]
for mk in sorted(H.market.unique()):
    out.append(res(H[(H.grade == "up") & (H.market == mk)], f"  👍 {mk}"))
for sd in ("over", "under"):
    out.append(res(H[(H.grade == "up") & (H.side == sd)], f"  👍 {sd}"))
for sg in ("High Shootout", "Lean Shootout", "Neutral", "Lean Grind", "Def Grind"):
    x = H[(H.grade == "up") & (H.script_grade == sg)]
    if len(x): out.append(res(x, f"  👍 script {sg}"))
Hn = grade(m, hold, T_UP, -T_UP, script=False); out.append(res(Hn[Hn.grade == "up"], "holdout: grade 👍 without script shift"))
print(pd.DataFrame(out).to_string(index=False))
H.to_csv(f"{OUT}/holdout_graded.csv", index=False)

# ---- B: March_edge actual pregame NFL picks Oct 2026 (never used for fitting) ----
B = pd.read_csv(f"{OUT}/B.csv"); B["push"] = False
B = prep(B); B = B[B.status.isin(["won", "lost"])]
B = grade(m, B, T_UP, -T_UP)
tw = np.where(B.is_home == 1, B.ph, 1 - B.ph)
B["marv_read"] = np.where(np.isnan(tw), "missing", np.where(tw > 0.5, "backs", "opposes"))
bo = [res(B, "(c) March_edge picks, no Marv filter"), res(B[B.marv_read != "opposes"], "(a) current: Marv game read not opposing"),
      res(B[B.marv_read == "backs"], "(a') Marv game read backs (strict)"), res(B[B.grade == "up"], "(b) new prop grade 👍"),
      res(B[B.grade == "neutral"], "    grade neutral"), res(B[B.grade == "down"], "    grade 👎"),
      res(B[(B.grade != "down")], "(b') grade not 👎")]
print("B window", B.date.min(), B.date.max()); print(pd.DataFrame(bo).to_string(index=False))
print(B.groupby(["grade", "marv_read"]).size())
B.to_csv(f"{OUT}/B_graded.csv", index=False)
