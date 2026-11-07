"""Walk-forward test of the in-game prop models. Train = earlier seasons, test = the latest season (never seen).

  1. remaining-stat accuracy (MAE, Poisson deviance) per model, stat, break
  2. probability quality at Kalshi-style ladders (Brier, calibration table)
  3. real-market test vs Kalshi prices at the breaks (if the price file exists):
       Brier of each model vs the market's own mid price on the same contracts,
       and paper bets (YES at the ask / NO at 1 - bid, Kalshi taker fee 0.07 p (1-p)) when edge >= threshold.
       Thresholds / the model-market blend are chosen on the first 40% of the price sample (by date) and reported
       on the last 60% only.

usage: python research/prop_pacer/evaluate.py SNAP.parquet [KALSHI.jsonl] OUT_DIR
"""

import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as M  # noqa: E402

LADDER = {"pts": [10, 15, 20, 25, 30, 35, 40], "reb": [4, 6, 8, 10, 12, 14], "ast": [2, 4, 6, 8, 10, 12],
          "fg3": [1, 2, 3, 4, 5, 6]}
MODELS = ["naive", "prop1", "v2", "ml"]


def norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    for x in (".", "'", "-", ",", " jr", " sr", " iii", " ii", " iv"):
        n = n.replace(x, " " if x == "-" else "")
    return " ".join(n.split())


def fee(p):
    return 0.07 * p * (1 - p)


def means(d: pd.DataFrame, fitted: dict) -> dict:
    out = {}
    for s in M.STATS:
        out[("naive", s)] = np.maximum(M.naive(d, s), 1e-4)
        out[("prop1", s)] = np.maximum(M.prop1(d, s), 1e-4)
        out[("v2", s)] = M.v2_mean(d, s, fitted["v2"])
        out[("ml", s)] = M.ml_mean(fitted["ml"][s], d)
    return out


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def main(snap: str, *rest: str) -> None:
    out_dir = Path(rest[-1])
    kalshi = rest[0] if len(rest) == 2 else None
    out_dir.mkdir(parents=True, exist_ok=True)
    df = M.usable(pd.read_parquet(snap))
    test_season = df["season"].max()
    train, test = df[df["season"] < test_season], df[df["season"] == test_season]
    print(f"train {len(train):,} rows (seasons {sorted(train.season.unique())}), test {len(test):,} rows ({test_season})")
    fitted = {"v2": M.fit_v2(train.sample(min(len(train), 120000), random_state=0))}
    print("v2 parameters:", {k: round(v, 3) for k, v in fitted["v2"].items()})
    fitted["ml"] = {s: M.fit_ml(train, s) for s in M.STATS}
    mtr, mte = means(train, fitted), means(test, fitted)
    disp = {key: M.fit_dispersion(mtr[key], train[f"{key[1]}_rem"].to_numpy()) for key in mtr}

    report = {"league": str(df["league"].iloc[0]), "train_rows": len(train), "test_rows": len(test), "test_season": int(test_season),
              "v2_params": fitted["v2"], "accuracy": [], "brier": [], "calibration": {}, "market": {}}
    # 1. accuracy
    for s in M.STATS:
        y = test[f"{s}_rem"].to_numpy()
        for k in sorted(test["k"].unique()):
            m = (test["k"] == k).to_numpy()
            row = {"stat": s, "k": int(k), "n": int(m.sum())}
            for name in MODELS:
                mu = mte[(name, s)][m]
                row[f"{name}_mae"] = float(np.mean(np.abs(mu - y[m])))
                row[f"{name}_dev"] = float(np.mean(2 * (np.where(y[m] > 0, y[m] * np.log(np.maximum(y[m], 1e-9) / mu), 0) - (y[m] - mu))))
            report["accuracy"].append(row)
    # 2. ladder Brier + calibration
    for s in M.STATS:
        for name in MODELS:
            ps, ys = [], []
            for T in LADDER[s]:
                need = T - test[f"{s}_sf"].to_numpy()
                keep = need > 0
                p = M.p_at_least(mte[(name, s)][keep], need[keep], disp[(name, s)])
                ps.append(p)
                ys.append((test[f"{s}_final"].to_numpy()[keep] >= T).astype(float))
            p, y = np.concatenate(ps), np.concatenate(ys)
            report["brier"].append({"stat": s, "model": name, "n": int(len(p)), "brier": float(np.mean((p - y) ** 2)),
                                    "logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))})
            bins = np.linspace(0, 1, 11)
            idx = np.clip(np.digitize(p, bins) - 1, 0, 9)
            report["calibration"][f"{name}:{s}"] = [
                {"bin": f"{bins[b]:.1f}-{bins[b + 1]:.1f}", "n": int((idx == b).sum()),
                 "pred": float(p[idx == b].mean()) if (idx == b).any() else None,
                 "actual": float(y[idx == b].mean()) if (idx == b).any() else None} for b in range(10)]
    # 3. Kalshi
    if kalshi and Path(kalshi).exists():
        report["market"] = market_test(test, mte, disp, kalshi)
    (out_dir / "prop_pacer_backtest.json").write_text(json.dumps(report, indent=1, default=float))
    write_md(report, out_dir / "prop_pacer_backtest.md")
    print((out_dir / "prop_pacer_backtest.md").read_text())


def market_test(test: pd.DataFrame, mte: dict, disp: dict, kalshi: str) -> dict:
    k = pd.DataFrame([json.loads(l) for l in open(kalshi)])
    k = k[k["yes_bid"].notna() & k["yes_ask"].notna() & k["result"].isin(["yes", "no"])]
    k["key"] = k["player_name"].map(norm)
    t = test.reset_index(drop=True).copy()
    t["key"] = t["name"].map(norm)
    t["_row"] = np.arange(len(t))
    j = k.merge(t[["game_id", "k", "key", "_row", "date", "pts_sf", "reb_sf", "ast_sf", "fg3_sf", "pts_final",
                   "reb_final", "ast_final", "fg3_final"]], on=["game_id", "k", "key"], how="inner")
    if j.empty:
        return {"matched": 0}
    j["so_far"] = [r[f"{s}_sf"] for r, s in zip(j.to_dict("records"), j["stat"])]
    j["final"] = [r[f"{s}_final"] for r, s in zip(j.to_dict("records"), j["stat"])]
    j["need"] = j["strike"] + 0.5 - j["so_far"]
    j["y"] = (j["result"] == "yes").astype(float)
    # sanity: settlement agrees with the play-by-play box score
    agree = float(((j["final"] > j["strike"]).astype(float) == j["y"]).mean())
    j = j[j["need"] > 0]  # already-cashed rungs are not bets
    j["mid"] = (j["yes_bid"] + j["yes_ask"]) / 2
    for name in MODELS:
        p = np.empty(len(j))
        for s in M.STATS:
            m = (j["stat"] == s).to_numpy()
            if m.any():
                rows = j.loc[m, "_row"].to_numpy()
                p[m] = M.p_at_least(mte[(name, s)][rows], j.loc[m, "need"].to_numpy(), disp[(name, s)])
        j[f"p_{name}"] = p
    j = j.sort_values("date")
    cut = int(len(j) * 0.4)
    cal, ev = j.iloc[:cut].copy(), j.iloc[cut:].copy()
    res = {"matched": int(len(j)), "settle_agree": agree, "games": int(j["game_id"].nunique()),
           "calib_rows": int(len(cal)), "eval_rows": int(len(ev)),
           "eval_dates": [str(ev["date"].min()), str(ev["date"].max())], "brier": {}, "bets": {}}
    res["brier"]["market_mid"] = float(np.mean((ev["mid"] - ev["y"]) ** 2))
    for name in MODELS:
        res["brier"][name] = float(np.mean((ev[f"p_{name}"] - ev["y"]) ** 2))
        # blend with the market: logistic fit on the calibration part only
        from sklearn.linear_model import LogisticRegression
        X = np.c_[logit(cal[f"p_{name}"]), logit(cal["mid"])]
        lr = LogisticRegression(C=10).fit(X, cal["y"])
        ev[f"p_{name}_blend"] = lr.predict_proba(np.c_[logit(ev[f"p_{name}"]), logit(ev["mid"])])[:, 1]
        res["brier"][f"{name}_blend"] = float(np.mean((ev[f"p_{name}_blend"] - ev["y"]) ** 2))
        res["brier"][f"{name}_blend_weights"] = [float(x) for x in lr.coef_[0]]
    for col in [f"p_{n}" for n in MODELS] + [f"p_{n}_blend" for n in MODELS]:
        res["bets"][col] = {}
        for thr in (0.02, 0.04, 0.06, 0.08, 0.10, 0.15):
            res["bets"][col][str(thr)] = bet(ev, col, thr)
            res["bets"][col][f"calib_{thr}"] = bet(cal, col, thr)
    return res


def bet(d: pd.DataFrame, col: str, thr: float) -> dict:
    p = d[col].to_numpy()
    ya, yb, y = d["yes_ask"].to_numpy(), d["yes_bid"].to_numpy(), d["y"].to_numpy()
    na = 1 - yb
    ok = (ya - yb <= 0.10) & (yb >= 0.02) & (ya <= 0.98)
    e_yes = p - ya - fee(ya)
    e_no = (1 - p) - na - fee(na)
    buy_yes = ok & (e_yes >= thr) & (e_yes >= e_no) & (ya >= 0.05)
    buy_no = ok & (e_no >= thr) & (e_no > e_yes) & (na >= 0.05)
    cost = np.where(buy_yes, ya + fee(ya), np.where(buy_no, na + fee(na), 0))
    win = np.where(buy_yes, y, np.where(buy_no, 1 - y, 0))
    n = int((buy_yes | buy_no).sum())
    staked = float(cost.sum())
    pnl = float((win * (buy_yes | buy_no)).sum() - staked)
    return {"n": n, "yes": int(buy_yes.sum()), "no": int(buy_no.sum()),
            "win_rate": float(win[buy_yes | buy_no].mean()) if n else None,
            "staked": round(staked, 2), "pnl": round(pnl, 2), "roi": pnl / staked if staked else None}


def write_md(r: dict, path: Path) -> None:
    L = [f"# In-game {r.get('league', 'nba').upper()} prop pacer backtest (test season {r['test_season']}, never seen in training)",
         f"train rows {r['train_rows']:,} · test rows {r['test_rows']:,}", "",
         "## 1. Remaining-stat accuracy (MAE, lower is better)", "",
         "| stat | break | n | naive | prop1 | v2 | ml |", "|---|---|---|---|---|---|---|"]
    for a in r["accuracy"]:
        L.append(f"| {a['stat']} | {'half' if r.get('league') == 'ncaab' else 'Q' + str(a['k'])} | {a['n']:,} | {a['naive_mae']:.2f} | {a['prop1_mae']:.2f} | "
                 f"{a['v2_mae']:.2f} | {a['ml_mae']:.2f} |")
    L += ["", "## 2. Ladder probabilities (Brier, lower is better)", "", "| stat | naive | prop1 | v2 | ml |",
          "|---|---|---|---|---|"]
    for s in M.STATS:
        b = {x["model"]: x["brier"] for x in r["brier"] if x["stat"] == s}
        L.append(f"| {s} | " + " | ".join(f"{b[m]:.4f}" for m in MODELS) + " |")
    m = r.get("market") or {}
    if m.get("matched"):
        L += ["", f"## 3. Real Kalshi prices at the breaks ({m['games']} games, {m['matched']:,} contracts; "
                  f"evaluation part {m['eval_rows']:,} rows, {m['eval_dates'][0]} to {m['eval_dates'][1]})",
              f"Kalshi settlement agrees with the play-by-play box score on {m['settle_agree']:.1%} of contracts.", "",
              "Brier on the evaluation part (lower is better; the market's own mid price is the bar):", ""]
        for k2, v in m["brier"].items():
            if not k2.endswith("weights"):
                L.append(f"- {k2}: {v:.4f}")
        L += ["", "Paper bets on the evaluation part (YES at ask / NO at 1 - bid, after Kalshi fee):", "",
              "| model | edge >= | bets | win rate | staked $ | P&L $ | ROI | (calibration part ROI) |",
              "|---|---|---|---|---|---|---|---|"]
        for col, by in m["bets"].items():
            for thr in ("0.04", "0.06", "0.08", "0.1"):
                b, c = by.get(thr), by.get(f"calib_{thr}")
                if not b or not b["n"]:
                    continue
                croi = f"{c['roi']:+.1%}" if c and c["roi"] is not None else "-"
                L.append(f"| {col[2:]} | {thr} | {b['n']} | {b['win_rate']:.1%} | {b['staked']:.0f} | {b['pnl']:+.1f} | "
                         f"{b['roi']:+.1%} | {croi} |")
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
