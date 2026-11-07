"""Design v3: market pregame anchor + minutes model + a small learned correction of the in-game market price.

Protocol (fixed before any v3 result was seen):
  model fitting      NBA seasons 2023-24 and 2024-25 (minutes model + dispersion), as before
  development (dev)  2025-26 games before 2026-04-01: fit the correction model, try ideas
  validation (val)   2025-26 games from 2026-04-01: choose the design and the edge threshold
  holdout            2026-27 games (Oct 20 - Nov 6 2026): evaluated ONCE, with --holdout, for the chosen design only

Pieces
  1. Market pregame anchor: each player's pregame Kalshi ladder (P(X >= rung) at tip-off) -> the mean of a negative
     binomial with the training dispersion that best matches the ladder (least squares on rungs with a tight quote).
  2. Minutes model (models.v2_mean) with the pregame per-minute rate taken from that anchor instead of the player's
     own average (anchor / usual minutes). Falls back to the average when there is no usable ladder.
  3. Correction: logistic regression on dev rows, outcome = contract settles YES, inputs = logit(break mid),
     logit(v3 model probability), logit(pregame mid of the same rung), "hot start" = stat so far minus the anchor's
     expectation so far (in sd units), break number, YES/NO-side-free. Small, L2-regularised, 6 inputs.
  Bets: YES at the ask / NO at 1 - bid, Kalshi taker fee 0.07 p(1-p), bid/ask gap <= 10c, quote refreshed after the
  period ended, edge >= threshold. Game-level bootstrap for uncertainty.

usage: python research/prop_pacer/market_v3.py SNAP4.parquet KALSHI_DEV.jsonl [KALSHI_HOLDOUT.jsonl --holdout] OUT_DIR
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats as sst

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as M  # noqa: E402
from evaluate import fee, logit, norm  # noqa: E402

VAL_START = "2026-04-01"
FEATS = ["l_mid", "l_model", "l_pre", "hot", "k2", "k3"]


def nb_sf(mu, need, phi):
    r = mu / max(phi - 1, 1e-3)
    return sst.nbinom.sf(np.ceil(need) - 1, r, r / (r + mu))


def ladder_mean(rungs: np.ndarray, probs: np.ndarray, phi: float) -> float | None:
    """Mean of NB(var = phi x mean) whose P(X >= rung) best matches the pregame ladder."""
    if len(rungs) == 0:
        return None
    f = lambda mu: np.sum((nb_sf(mu, rungs, phi) - probs) ** 2)
    grid = np.geomspace(0.05, 80, 160)  # grid first: a plain bounded search can walk into the flat tail
    i = int(np.argmin([f(m) for m in grid]))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    return float(optimize.minimize_scalar(f, bounds=(lo, hi), method="bounded").x)


def load_prices(path: str) -> pd.DataFrame:
    k = pd.DataFrame([json.loads(line) for line in open(path)])
    k = k[k["result"].isin(["yes", "no"])].copy()
    k["key"] = k["player_name"].map(norm)
    k["y"] = (k["result"] == "yes").astype(float)
    k["rung"] = k["strike"] + 0.5  # "25+" has floor_strike 24.5
    return k


def anchors_from_ladder(k: pd.DataFrame, phi_pre: dict) -> pd.DataFrame:
    pre = k.drop_duplicates("ticker")
    pre = pre[pre["pre_bid"].notna() & pre["pre_ask"].notna() & (pre["pre_ask"] - pre["pre_bid"] <= 0.15)]
    pre = pre.assign(pre_mid=(pre["pre_bid"] + pre["pre_ask"]) / 2)
    rows = []
    for (gid, key, stat), g in pre.groupby(["game_id", "key", "stat"]):
        mu = ladder_mean(g["rung"].to_numpy(float), g["pre_mid"].to_numpy(float), phi_pre[stat])
        rows.append({"game_id": gid, "key": key, "stat": stat, "mkt_mu": mu, "n_rungs": len(g)})
    return pd.DataFrame(rows)


def cv_dev(dev: pd.DataFrame, feats: list[str], folds: int = 5) -> np.ndarray:
    """Out-of-fold correction probabilities on dev, folds by game (no game is in its own training fold)."""
    from sklearn.linear_model import LogisticRegression
    games = np.array(sorted(dev["game_id"].unique()))
    rng = np.random.default_rng(1)
    rng.shuffle(games)
    fold_of = {g: i % folds for i, g in enumerate(games)}
    f = dev["game_id"].map(fold_of).to_numpy()
    out = np.full(len(dev), np.nan)
    for i in range(folds):
        tr = (f != i) & dev["tight"].to_numpy()
        lr = LogisticRegression(C=1.0).fit(dev.loc[tr, feats], dev.loc[tr, "y"])
        out[f == i] = lr.predict_proba(dev.loc[f == i, feats])[:, 1]
    return out


def main(snap: str, dev_path: str, *rest: str) -> None:
    holdout = "--holdout" in rest
    cv_only = "--cv" in rest
    rest = [r for r in rest if r not in ("--holdout", "--cv")]
    out_dir = Path(rest[-1])
    hold_path = rest[0] if len(rest) == 2 else None
    out_dir.mkdir(parents=True, exist_ok=True)
    df = M.usable(pd.read_parquet(snap))
    train = df[df["season"] <= 2025]
    cache = out_dir / "v2_params_train_2024_2025.json"  # fit on train seasons only; cached because it takes minutes
    if cache.exists():
        params = json.loads(cache.read_text())
    else:
        params = M.fit_v2(train.sample(min(len(train), 120000), random_state=0))
        cache.write_text(json.dumps(params))
    disp = {s: M.fit_dispersion(M.v2_mean(train, s, params), train[f"{s}_rem"].to_numpy()) for s in M.STATS}
    # pregame dispersion of the full-game stat around a pregame mean (for reading the ladder)
    t1 = train[train["k"] == 1]
    phi_pre = {s: float(np.mean((t1[f"{s}_final"] - t1[f"pre_{s}"]) ** 2) / max(t1[f"pre_{s}"].mean(), 1e-6))
               for s in M.STATS}
    print("pregame dispersion", {s: round(v, 2) for s, v in phi_pre.items()})

    def build(path: str, season: int) -> pd.DataFrame:
        k = load_prices(path)
        anc = anchors_from_ladder(k, phi_pre)
        snap_s = df[df["season"] == season].copy()
        snap_s["key"] = snap_s["name"].map(norm)
        j = k.merge(snap_s, on=["game_id", "k", "key"], how="inner", suffixes=("", "_snap"))
        j = j.merge(anc, on=["game_id", "key", "stat"], how="left")
        j["so_far"] = [r[f"{s}_sf"] for r, s in zip(j.to_dict("records"), j["stat"])]
        j["need"] = j["rung"] - j["so_far"]
        j = j[(j["need"] > 0) & j["yes_bid"].notna() & j["yes_ask"].notna()].copy()
        j["mid"] = (j["yes_bid"] + j["yes_ask"]) / 2
        j["pre_mid"] = (j["pre_bid"] + j["pre_ask"]) / 2
        p_avg, p_mkt = np.empty(len(j)), np.empty(len(j))
        hot = np.zeros(len(j))
        for s in M.STATS:
            m = (j["stat"] == s).to_numpy()
            if not m.any():
                continue
            sub = j.loc[m].copy()
            p_avg[m] = M.p_at_least(M.v2_mean(sub, s, params), sub["need"].to_numpy(), disp[s])
            anchor = sub["mkt_mu"].where(sub["mkt_mu"].notna(), sub[f"pre_{s}"])
            sub[f"pre_{s}"] = anchor  # market's pregame mean replaces the player's own average
            p_mkt[m] = M.p_at_least(M.v2_mean(sub, s, params), sub["need"].to_numpy(), disp[s])
            exp_sf = anchor * (sub["min_sf"] / sub["pre_min"].clip(lower=1))
            hot[m] = ((sub["so_far"] - exp_sf) / np.sqrt(np.maximum(exp_sf * phi_pre[s], 0.5))).to_numpy()
        j["p_minutes"], j["p_v3"], j["hot"] = p_avg, p_mkt, hot
        j["l_mid"], j["l_model"] = logit(j["mid"]), logit(j["p_v3"])
        j["l_pre"] = logit(j["pre_mid"].fillna(j["mid"]))
        j["k2"], j["k3"] = (j["k"] == 2).astype(float), (j["k"] == 3).astype(float)
        j["fresh"] = j["since_period_end_s"] >= 0
        j["tight"] = (j["yes_ask"] - j["yes_bid"] <= 0.10) & (j["yes_bid"] >= 0.02) & (j["yes_ask"] <= 0.98)
        return j

    d = build(dev_path, 2026)
    dev, val = d[d["date"] < VAL_START].copy(), d[d["date"] >= VAL_START].copy()
    if cv_only:  # development only: nothing from validation or holdout is computed or printed
        dev["p_corr"] = cv_dev(dev, FEATS)
        q = dev[dev["tight"] & dev["fresh"]]
        print(f"DEV ONLY (out-of-fold by game): {len(q):,} tradeable fresh quotes, {q.game_id.nunique()} games")
        print("Brier: " + ", ".join(f"{c} {np.mean((q[c] - q['y']) ** 2):.4f}" for c in ("mid", "p_minutes", "p_v3", "p_corr")))
        for col in ("p_minutes", "p_v3", "p_corr"):
            for side in ("both", "no"):
                for thr in (0.02, 0.04, 0.06, 0.08):
                    b = boot(q, col, thr, side)
                    if b["n"]:
                        print(f"  {col:9s} {side:4s} edge>={thr:.2f}: {b['n']:4d} bets ROI {b['roi']:+.1%} "
                              f"[{b['lo']:+.1%} .. {b['hi']:+.1%}] P>0 {b['p_pos']:.0%}")
        dev.to_parquet(out_dir / "dev_rows.parquet")
        return
    print(f"dev {len(dev):,} contracts / {dev.game_id.nunique()} games, val {len(val):,} / {val.game_id.nunique()} games;"
          f" market anchor found for {d['mkt_mu'].notna().mean():.0%}")
    from sklearn.linear_model import LogisticRegression
    fit_rows = dev[dev["tight"]]
    lr = LogisticRegression(C=1.0).fit(fit_rows[FEATS], fit_rows["y"])
    coef = dict(zip(FEATS, np.round(lr.coef_[0], 3)))
    print("correction model weights", coef)
    for part in (dev, val):
        part["p_corr"] = lr.predict_proba(part[FEATS])[:, 1]

    rep = {"coef": coef, "phi_pre": phi_pre, "parts": {}}
    lines = ["# Design v3 (market anchor + minutes model + correction) — NBA Kalshi", "",
             f"Correction weights (fit on dev only): {coef}", ""]
    parts = [("dev (fit)", dev), ("validation", val)]
    if holdout and hold_path:
        h = build(hold_path, 2027)
        h["p_corr"] = lr.predict_proba(h[FEATS])[:, 1]
        parts.append(("HOLDOUT 2026-27", h))
    for name, part in parts:
        q = part[part["tight"] & part["fresh"]]
        r = {"rows": int(len(q)), "games": int(q["game_id"].nunique()), "brier": {}, "bets": {}}
        for col in ("mid", "p_minutes", "p_v3", "p_corr"):
            r["brier"][col] = float(np.mean((q[col] - q["y"]) ** 2)) if len(q) else None
        for col in ("p_minutes", "p_v3", "p_corr"):
            for side in ("both", "no"):
                for thr in (0.02, 0.04, 0.06, 0.08):
                    r["bets"][f"{col}|{side}|{thr}"] = boot(q, col, thr, side)
        rep["parts"][name] = r
        lines += [f"## {name}: {r['rows']:,} tradeable fresh quotes, {r['games']} games", "",
                  "Brier: " + ", ".join(f"{k2} {v:.4f}" for k2, v in r["brier"].items() if v is not None), "",
                  "| model | side | edge >= | bets | ROI | 90% range | P(ROI>0) |", "|---|---|---|---|---|---|---|"]
        for key, b in r["bets"].items():
            if b["n"]:
                col, side, thr = key.split("|")
                lines.append(f"| {col} | {side} | {thr} | {b['n']} | {b['roi']:+.1%} | {b['lo']:+.1%} .. {b['hi']:+.1%} | "
                             f"{b['p_pos']:.0%} |")
        lines.append("")
    (out_dir / "v3.json").write_text(json.dumps(rep, indent=1, default=float))
    (out_dir / "v3.md").write_text("\n".join(lines))
    print("\n".join(lines))


def boot(q: pd.DataFrame, col: str, thr: float, side: str, n_boot: int = 1000) -> dict:
    p, ya, yb, y = q[col].to_numpy(), q["yes_ask"].to_numpy(), q["yes_bid"].to_numpy(), q["y"].to_numpy()
    na = 1 - yb
    e_yes, e_no = p - ya - fee(ya), (1 - p) - na - fee(na)
    by = (e_yes >= thr) & (e_yes >= e_no) & (ya >= 0.05) & (side == "both")
    bn = (e_no >= thr) & (e_no > e_yes if side == "both" else True) & (na >= 0.05)
    take = by | bn
    if not take.any():
        return {"n": 0}
    cost = np.where(by, ya + fee(ya), na + fee(na))[take]
    pnl = np.where(by, y, 1 - y)[take] - cost
    g = pd.DataFrame({"g": q["game_id"].to_numpy()[take], "pnl": pnl, "cost": cost}).groupby("g").sum()
    rng = np.random.default_rng(0)
    rois = []
    for _ in range(n_boot):
        s = g.iloc[rng.integers(0, len(g), len(g))].sum()
        rois.append(s["pnl"] / s["cost"])
    return {"n": int(take.sum()), "roi": float(pnl.sum() / cost.sum()), "lo": float(np.percentile(rois, 5)),
            "hi": float(np.percentile(rois, 95)), "p_pos": float(np.mean(np.array(rois) > 0))}


if __name__ == "__main__":
    main(*sys.argv[1:])
