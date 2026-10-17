"""Pre-specified grade variants. Pick on the tune period only (two inner folds), then report every variant on holdout."""
import numpy as np, pandas as pd
from analyze_lib import *
A = prep(pd.read_csv(f"{OUT}/A.csv")); A = A[~A.push.astype(bool)]
tune, hold = A[A.week <= 6], A[A.week >= 7]
F_ALL = ["f_model", "f_form", "f_def", "f_usage", "f_team", "under", "f_mkt"]
F_NOFORM = ["f_model", "f_def", "f_usage", "f_team", "under", "f_mkt"]
def votes(d):
    v = (d.f_model > 0.10).astype(int) + (d.f_form > 0.10).astype(int) + (d.f_def > 0.05).astype(int) \
        + (d.script_shift > 0.004).astype(int) + (d.f_team > 0.15).astype(int) + d.under
    a = (d.f_model < -0.10).astype(int) + (d.f_form < -0.10).astype(int) + (d.f_def < -0.05).astype(int) + (d.script_shift < -0.004).astype(int)
    return v, a
def run(var, tr, te):
    if var.startswith("vote"):
        k = int(var[-1]); v, a = votes(te)
        g = np.where((v >= k) & (a <= 1) & (te.price >= -160), "up", np.where(v <= 1, "down", "neutral"))
        return te.assign(grade=g)
    feats, t, sc = {"LR_all_ev3": (F_ALL, .03, True), "LR_all_ev0": (F_ALL, 0, True), "LR_all_ev3_noscript": (F_ALL, .03, False),
                    "LR_noform_ev3": (F_NOFORM, .03, True), "LR_noform_ev0": (F_NOFORM, 0, True)}[var]
    m = fit(tr, feats=feats); return grade(m, te, t, -max(t, .03), script=sc, feats=feats)
VARS = ["LR_all_ev3", "LR_all_ev0", "LR_all_ev3_noscript", "LR_noform_ev3", "LR_noform_ev0", "vote3", "vote4"]
rows = []
for var in VARS:
    r1 = res(run(var, tune[tune.week <= 4], tune[tune.week.isin([5, 6])]).query("grade=='up'"), var)
    r2 = res(run(var, tune[tune.week <= 3], tune[tune.week == 4]).query("grade=='up'"), var)
    rh = res(run(var, tune, hold).query("grade=='up'"), var)
    rows.append({"variant": var, "inner1 n": r1["n"], "inner1 ROI": r1["ROI%"], "inner2 n": r2["n"], "inner2 ROI": r2["ROI%"],
                 "tune avg ROI": round(np.nanmean([r1["ROI%"], r2["ROI%"]]), 1), "HOLDOUT n": rh["n"], "HOLDOUT W-L": f'{rh["W"]}-{rh["L"]}',
                 "HOLDOUT win%": rh["win%"], "HOLDOUT ROI": rh["ROI%"]})
print(pd.DataFrame(rows).to_string(index=False))
