"""Final out-of-sample tables. Grade = LR_all_ev3 (chosen on the tune period only, see variants.py)."""
import sqlite3, json, numpy as np, pandas as pd
from analyze_lib import *
T = 0.03
A = prep(pd.read_csv(f"{OUT}/A.csv")); A = A[~A.push.astype(bool)]
tune, hold = A[A.week <= 6], A[A.week >= 7]
m6 = fit(tune); m10 = fit(A)          # m6 for 2025 wk7-10 tests; m10 (all 2025, older than Oct 2026) for the 2026 picks
json.dump({"feats": FEATS, "coef": m10.coef_[0].tolist(), "intercept": float(m10.intercept_[0]), "t_up": T, "t_dn": -T,
           "prop_cap": PROP_CAP, "th_nfl": TH, "fit": "2025 NFL wk1-10 real lines (props_backtest_real), both sides, pushes dropped",
           "n_fit": int(len(A))}, open(f"{OUT}/model_live.json", "w"), indent=1)
def marv_read(d):
    tw = np.where(d.is_home == 1, d.ph, 1 - d.ph)
    return np.where(np.isnan(tw.astype(float)), "missing", np.where(tw > 0.5, "backs", "opposes"))
def three(d, label):
    return [dict(res(d[d.marv_read != "opposes"], "(a) current: ME + Marv game read not opposing"), pop=label),
            dict(res(d[d.grade == "up"], "(b) new prop grade 👍 only"), pop=label),
            dict(res(d, "(c) no Marv filter"), pop=label)]
rows = []
# T1 all real lines 2025 wk7-10
H = grade(m6, hold, T, -T); H["marv_read"] = marv_read(H)
rows += three(H, "T1 every real NFL line 2025-10-16..11-10 (both sides)")
# T2 March_edge-rule replay 2025 wk7-10
C = pd.read_csv(f"{OUT}/C.csv"); C = C.merge(pd.read_csv(f"{OUT}/A.csv")[["game_id", "ph", "tot_hat"]].drop_duplicates("game_id"), on="game_id", how="left")
C = prep(C); C = C[~C.push.astype(bool)]
C2 = grade(m6, C[C.week >= 7], T, -T); C2["marv_read"] = marv_read(C2)
rows += three(C2, "T2 March_edge rule replayed, 2025-10-16..11-10")
# T3 March_edge actual pregame NFL picks Oct 2026
B = pd.read_csv(f"{OUT}/B.csv"); B["push"] = False; B = prep(B); B = B[B.status.isin(["won", "lost"])]
B3 = grade(m10, B, T, -T); B3["marv_read"] = marv_read(B3)
rows += three(B3, "T3 March_edge actual NFL pregame picks 2026-10-02..10-13")
both = pd.concat([C2.assign(src="T2"), B3.assign(src="T3")], ignore_index=True)
rows += three(both, "T2+T3 March_edge picks combined (NFL)")
# other sports (no player model -> grade neutral; game read not reconstructable -> kept by (a))
allb = pd.read_csv(f"{R if 'R' in dir() else '/opt/marv-bot'}/research/propgrade_v1/out/B_all_picks.csv")
ob = allb[(allb.phase == "pregame") & allb.status.isin(["won", "lost"]) & (allb.league != "nfl")].copy()
ob["won"] = ob.status == "won"; ob["pay"] = payout(ob.price)
for lgx, g in ob.groupby("league"):
    rows += [dict(res(g, "(a) current"), pop=f"T3 {lgx} pregame (Marv read n/a -> kept)"), dict({"set": "(b) 👍", "n": 0, "W": 0, "L": 0, "win%": None, "ROI%": None}, pop=f"T3 {lgx}: no player model -> never 👍"),
             dict(res(g, "(c) no Marv filter"), pop=f"T3 {lgx}")]
df = pd.DataFrame(rows)[["pop", "set", "n", "W", "L", "win%", "ROI%"]]
print(df.to_string(index=False))
# extra detail
x = [res(H[H.grade == g], f"T1 grade {g}") for g in ("up", "neutral", "down")]
x += [res(H[(H.grade == "up") & (H.market == mk)], f"T1 👍 {mk}") for mk in sorted(H.market.unique())]
x += [res(H[(H.grade == "up") & (H.side == s)], f"T1 👍 {s}") for s in ("over", "under")]
x += [res(H[H.side == "under"], "T1 always under")]
x += [res(B3[B3.grade == g], f"T3 grade {g}") for g in ("up", "neutral", "down")]
x += [res(both[(both.grade == "up") & (both.marv_read != "opposes")], "T2+T3 (a) AND (b)")]
print(pd.DataFrame(x).to_string(index=False))
print("T3 marv_read source:", B3.src_read.fillna("pick_engine walk-forward / missing").value_counts().to_dict(), B3.marv_read.value_counts().to_dict())
df.to_csv(f"{OUT}/final_table.csv", index=False); pd.DataFrame(x).to_csv(f"{OUT}/final_detail.csv", index=False)
both.to_csv(f"{OUT}/me_picks_graded.csv", index=False)
print("\n--- March_edge 2-star+ only (EV >= 7%) and strict 'backs' ---")
y = []
for nm, d in (("T2", C2), ("T3", B3), ("T2+T3", both)):
    s2 = d[d.ev >= 0.07]
    y += [res(s2[s2.marv_read != "opposes"], f"{nm} 2★+ (a) not opposing"), res(s2[s2.marv_read == "backs"], f"{nm} 2★+ (a') strict backs"),
          res(s2[s2.grade == "up"], f"{nm} 2★+ (b) 👍"), res(s2, f"{nm} 2★+ (c) all")]
    y += [res(d[d.marv_read == "backs"], f"{nm} EV5+ (a') strict backs")]
print(pd.DataFrame(y).to_string(index=False)); pd.DataFrame(y).to_csv(f"{OUT}/final_2star.csv", index=False)
print("T2 replay window", C2.date.min(), C2.date.max(), "| T3 window", B3.date.min(), B3.date.max())
print("T3 by market (b):"); print(B3[B3.grade=="up"].groupby("market").won.agg(["size","sum"]))
