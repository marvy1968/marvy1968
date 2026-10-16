import numpy as np, pandas as pd, json
from ou_signals import grade
D = pd.read_csv("ou_games_cfb.csv", parse_dates=["date"])
def fit(tr, fs):
    X = tr[fs+["y"]].dropna(); mu, sd = X[fs].mean(), X[fs].std()
    Z = np.c_[np.ones(len(X)), ((X[fs]-mu)/sd).values]; b = np.linalg.lstsq(Z, X.y.values, rcond=None)[0]
    return lambda Y: b[0]+(((Y[fs]-mu)/sd).fillna(0).values @ b[1:]), b, mu, sd
side = lambda p, k: np.where(p >= k, 1, np.where(p <= -k, -1, 0))
out = {}
for split in (2023, 2022, 2024):
    tr, te = D[D.season < split], D[D.season >= split]
    cur_te = (te.cur*te.cur_side).values.astype(int)
    r = {"CURRENT_test": grade(te, cur_te)}
    for nm, fs, k in (("LINE", ["f_close"], 0.75), ("TREND_D", ["f_trend_ovr10", "f_d"], 1.75)):
        p, b, mu, sd = fit(tr, fs)
        r[nm] = dict(coef=[round(float(x), 3) for x in b], mu=[round(float(x), 3) for x in mu], sd=[round(float(x), 3) for x in sd],
                     train=grade(tr, side(p(tr), k)), test=grade(te, side(p(te), k)))
    out[split] = r
print(json.dumps(out, indent=0))
tr, te = D[D.season < 2023], D[D.season >= 2023]
for fs in (["f_close", "f_trend_ovr10"], ["f_close", "f_trend_ou5"]):
    p, b, mu, sd = fit(tr, fs)
    for k in (0.75, 1.0, 1.25, 1.5):
        g1, g2 = grade(tr, side(p(tr), k)), grade(te, side(p(te), k))
        print(fs, k, "train", g1["n"], g1["hit"], g1["roi"], "test", g2["n"], g2["hit"], g2["roi"])
lc = D.groupby("date").close.mean(); print("lgclose latest", D.lgclose.iloc[-1], D.date.iloc[-1], "games-mean last365", D[D.date > D.date.max()-pd.Timedelta(days=365)].close.mean())
