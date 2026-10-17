"""Calibration check of Marv's live soccer goal model at the half-time state (unseen games only).

Data: Marv's walk-forward pregame xG (soccer_validation_predictions.csv.gz: each season's xG comes from ratings and
params fitted on earlier data only) joined with football-data.co.uk half-time / full-time scores (main leagues; the
MLS / Liga MX files have no HT score).
Train: every prediction before the test window -> fit F2 (2nd-half goal share) and lead/trail multipliers.
Test : the last 30 days of predictions (never used in any fit). No odds exist for these states, so this is a
probability calibration check (Brier / log loss), not a profit backtest.
Baseline: same live maths but each team's xG = league home/away scoring average (no Marv team strength).
"""
import json, math, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "/opt/marv-bot")
from marv.soccer_max import data as D, live_props as LP

ST = Path("/opt/marv-bot/state")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/slp_calib")
OUT.mkdir(parents=True, exist_ok=True)
pr = pd.read_csv(ST / "marv_predict/soccer/soccer_validation_predictions.csv.gz", parse_dates=["date"])

rows = []
for key, (code, _, kind) in D.LEAGUES.items():
    if kind != "main":
        continue
    for f in sorted((ST / "cache/soccer_fd").glob(f"{code}_*.csv")):
        try:
            d = pd.read_csv(f, encoding="latin-1", usecols=lambda c: c.strip("\ufeffï»¿") in
                            ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG"))
        except Exception:
            continue
        d.columns = [c.strip("\ufeffï»¿") for c in d.columns]
        d["date"] = pd.to_datetime(d["Date"], dayfirst=True, errors="coerce")
        d["league"] = key
        rows.append(d.rename(columns={"HomeTeam": "home", "AwayTeam": "away"}))
ht = pd.concat(rows).dropna(subset=["date", "HTHG", "HTAG", "FTHG", "FTAG"])
df = pr.merge(ht[["league", "date", "home", "away", "HTHG", "HTAG", "FTHG", "FTAG"]], on=["league", "date", "home", "away"])
df = df[(df["FTHG"] == df["hg"]) & (df["FTAG"] == df["ag"])].copy()
end = df["date"].max()
start = end - pd.Timedelta(days=30)
tr, te = df[df["date"] < start].copy(), df[df["date"] >= start].copy()
print(f"joined {len(df)} matches with HT score; train {len(tr)} (<{start.date()}), test {len(te)} ({start.date()}..{end.date()})")

# ---- fit on train only
for x in (tr, te):
    x["h2"], x["a2"] = x["FTHG"] - x["HTHG"], x["FTAG"] - x["HTAG"]
lev = tr[tr["HTHG"] == tr["HTAG"]]
f2 = float((lev["h2"].sum() + lev["a2"].sum()) / (lev["lh"].sum() + lev["la"].sum()))
# team ahead / behind at HT: Poisson MLE with offset = ratio of observed to expected 2nd-half goals
def ratio(mask_h, mask_a):
    g = tr.loc[mask_h, "h2"].sum() + tr.loc[mask_a, "a2"].sum()
    e = (tr.loc[mask_h, "lh"].sum() + tr.loc[mask_a, "la"].sum()) * f2
    return math.log(g / e)
b_lead = ratio(tr["HTHG"] > tr["HTAG"], tr["HTAG"] > tr["HTHG"])
b_trail = ratio(tr["HTHG"] < tr["HTAG"], tr["HTAG"] < tr["HTHG"])
first_share = float((tr["HTHG"].sum() + tr["HTAG"].sum()) / (tr["FTHG"].sum() + tr["FTAG"].sum()))
fit = {"f2": round(f2, 4), "beta_lead": round(b_lead, 4), "beta_trail": round(b_trail, 4), "end_minute": 93.0}
print("fit (train):", fit, "| observed 1st-half goal share", round(first_share, 4))
# gamma-Poisson update of the game's scoring level from 1st-half goals vs Marv's 1st-half expectation:
# mult = (k + goals_1h) / (k + (lh + la) * (1 - f2)); k fit on train by log likelihood of 2nd-half goals.
def _bayes_ll(k, x):
    e1 = (x["lh"] + x["la"]) * (1 - f2)
    mult = (k + x["HTHG"] + x["HTAG"]) / (k + e1)
    ll = 0.0
    for side, lam0, g in (("h", x["lh"], x["h2"]), ("a", x["la"], x["a2"])):
        lead = (x["HTHG"] > x["HTAG"]) if side == "h" else (x["HTAG"] > x["HTHG"])
        trail = (x["HTHG"] < x["HTAG"]) if side == "h" else (x["HTAG"] < x["HTHG"])
        lam = lam0 * f2 * mult * np.exp(np.where(lead, b_lead, np.where(trail, b_trail, 0.0)))
        ll += float(np.sum(g * np.log(lam) - lam))
    return ll
KS = [1, 2, 3, 5, 8, 12, 20, 40, 1e9]
k_best = max(KS, key=lambda k: _bayes_ll(k, tr))
print("bayes k (train):", k_best, {k: round(_bayes_ll(k, tr), 1) for k in KS})
fit_b = dict(fit, bayes_k=float(k_best))
for nm, x in (("train", tr), ("test", te)):
    print(nm, "2H goals obs/exp (fit, no bayes):", round(float((x["h2"] + x["a2"]).sum() / ((x["lh"] + x["la"]).sum() * f2)), 3),
          "| FT goals obs/pregame xG:", round(float((x["FTHG"] + x["FTAG"]).sum() / (x["lh"] + x["la"]).sum()), 3))
lg_avg = tr.groupby("league").agg(mh=("FTHG", "mean"), ma=("FTAG", "mean"))

MODELS = {
    "marv_fit": lambda r: (r.lh, r.la, fit),
    "marv_overlay_93": lambda r: (r.lh, r.la, {"f2": 48 / 93, "beta_lead": 0.0, "beta_trail": 0.0, "end_minute": 93.0}),
    "marv_fit_bayes": lambda r: (r.lh, r.la, fit_b),
    "league_avg_fit": lambda r: (lg_avg.loc[r.league, "mh"], lg_avg.loc[r.league, "ma"], fit),
}
MKTS = [("total_o1.5", "t", 1.5), ("total_o2.5", "t", 2.5), ("total_o3.5", "t", 3.5), ("home_tt_o0.5", "h", 0.5),
        ("home_tt_o1.5", "h", 1.5), ("away_tt_o0.5", "a", 0.5), ("away_tt_o1.5", "a", 1.5), ("btts_yes", "b", None)]


def preds(x, mname):
    out = {m[0]: [] for m in MKTS}
    for r in x.itertuples():
        lh, la, prm = MODELS[mname](r)
        s = LP.price_state(lh, la, int(r.HTHG), int(r.HTAG), 45.0, prm, ht=True)
        for name, kind, ln in MKTS:
            if kind == "t":
                p = s["totals"][f"{ln:g}"]["over"]
            elif kind in "ha":
                p = s["team_totals"]["home" if kind == "h" else "away"][f"{ln:g}"]["over"]
            else:
                p = s["btts"]["yes"]
            out[name].append(p)
    return {k: np.array(v) for k, v in out.items()}


def outcome(x):
    t = x["FTHG"] + x["FTAG"]
    return {"total_o1.5": t > 1.5, "total_o2.5": t > 2.5, "total_o3.5": t > 3.5, "home_tt_o0.5": x["FTHG"] > 0.5,
            "home_tt_o1.5": x["FTHG"] > 1.5, "away_tt_o0.5": x["FTAG"] > 0.5, "away_tt_o1.5": x["FTAG"] > 1.5,
            "btts_yes": (x["FTHG"] > 0) & (x["FTAG"] > 0)}


def score(x, label):
    y = {k: v.values.astype(float) for k, v in outcome(x).items()}
    rep = {}
    for mname in MODELS:
        p = preds(x, mname)
        res = {}
        for k in y:
            # only states where the market is still open at HT (not already decided)
            q = np.clip(p[k], 1e-6, 1 - 1e-6)
            open_ = (q > 1e-5) & (q < 1 - 1e-5)
            yy, qq = y[k][open_], q[open_]
            res[k] = {"n": int(open_.sum()), "brier": round(float(np.mean((qq - yy) ** 2)), 4),
                      "logloss": round(float(-np.mean(yy * np.log(qq) + (1 - yy) * np.log(1 - qq))), 4),
                      "mean_p": round(float(qq.mean()), 4), "hit": round(float(yy.mean()), 4)}
        allq = np.concatenate([np.clip(p[k], 1e-6, 1 - 1e-6)[(p[k] > 1e-5) & (p[k] < 1 - 1e-5)] for k in y])
        ally = np.concatenate([y[k][(p[k] > 1e-5) & (p[k] < 1 - 1e-5)] for k in y])
        bins = np.clip((allq * 5).astype(int), 0, 4)
        rel = [{"bin": f"{b/5:.1f}-{(b+1)/5:.1f}", "n": int((bins == b).sum()),
                "pred": round(float(allq[bins == b].mean()), 3) if (bins == b).any() else None,
                "obs": round(float(ally[bins == b].mean()), 3) if (bins == b).any() else None} for b in range(5)]
        rep[mname] = {"markets": res, "pooled_brier": round(float(np.mean((allq - ally) ** 2)), 4),
                      "pooled_logloss": round(float(-np.mean(ally * np.log(allq) + (1 - ally) * np.log(1 - allq))), 4),
                      "pooled_n": int(len(ally)), "reliability": rel}
    return rep

report = {"generated": pd.Timestamp.utcnow().isoformat(), "version": LP.VERSION, "fit_on_train": fit,
          "train": {"n": len(tr), "end": str(start.date())}, "test": {"n": len(te), "start": str(start.date()), "end": str(end.date()),
          "leagues": te.groupby("league").size().to_dict()},
          "test_scores": score(te, "test"),
          "note": "HT state only (football-data has no in-play minute data); unseen = last 30 days of walk-forward preds"}
(OUT / "live_props_calib_report.json").write_text(json.dumps(report, indent=1))
(OUT / "live_props_calib.json").write_text(json.dumps({"params": fit, "fit_on": f"HT/FT train < {start.date()}",
                                                       "version": LP.VERSION}, indent=1))
for m, r in report["test_scores"].items():
    print(f"{m:18s} pooled n={r['pooled_n']} brier={r['pooled_brier']} logloss={r['pooled_logloss']}")
    for k, v in r["markets"].items():
        print(f"   {k:13s} n={v['n']:4d} brier={v['brier']:.4f} ll={v['logloss']:.4f} mean_p={v['mean_p']:.3f} hit={v['hit']:.3f}")
    print("   reliability:", r["reliability"])
