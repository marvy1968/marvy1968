"""Stale-rung test: price a player's NOT-refreshed Kalshi ladder rungs from the rungs that WERE refreshed after the
period ended (same player, same stat, same break). No player model is involved: the fresh rungs say what the market
now thinks; a stale rung that disagrees may be a resting quote nobody has updated.

For each (game, player, stat, break):
  fresh = rungs with a tight quote (gap <= 10c) refreshed after the period ended
  fit the mean of the remaining stat (negative binomial, training dispersion) to the fresh rungs' mid prices
  stale rungs with a tight quote -> fair = P(remaining >= need) under that fit -> bet if fair - cost >= edge
  also leave-one-out on fresh rungs (does the ladder price its own rungs coherently?)

usage: python research/prop_pacer/ladder.py DEV_ROWS.parquet SNAP4.parquet PARAMS.json [--part dev|val]
DEV_ROWS is written by market_v3.py --cv (or --rows) and carries need, mid, fresh, tight, y, stat, k.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as M  # noqa: E402
from evaluate import fee  # noqa: E402
from market_v3 import boot  # noqa: E402


def fit_mu(need: np.ndarray, mid: np.ndarray, disp) -> float:
    """Grid in log space, then a local refinement. (A plain bounded search walks off to the boundary: probabilities
    are clipped at 0.9999, so the loss is flat for large means and the first golden-section probes land there.)"""
    f = lambda mu: np.sum((M.p_at_least(np.full(len(need), mu), need, disp) - mid) ** 2)
    grid = np.geomspace(0.01, 60, 160)
    i = int(np.argmin([f(m) for m in grid]))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    return float(optimize.minimize_scalar(f, bounds=(lo, hi), method="bounded").x)


def main(rows_path: str, snap: str, params_path: str, *rest: str) -> None:
    d = pd.read_parquet(rows_path)
    if "--part" in rest:
        part = rest[rest.index("--part") + 1]
        d = d[d["date"] < "2026-04-01"] if part == "dev" else d[d["date"] >= "2026-04-01"]
        print(f"part: {part}")
    params = json.loads(Path(params_path).read_text())
    df = M.usable(pd.read_parquet(snap))
    train = df[df["season"] <= 2025]
    disp = {s: M.fit_dispersion(M.v2_mean(train, s, params), train[f"{s}_rem"].to_numpy()) for s in M.STATS}
    d = d[d["tight"]].copy()
    d["p_ladder"] = np.nan
    d["p_loo"] = np.nan
    for (_, _, stat, _), g in d.groupby(["game_id", "key", "stat", "k"]):
        fr = g[g["fresh"]]
        if len(fr) >= 1:
            mu = fit_mu(fr["need"].to_numpy(float), fr["mid"].to_numpy(float), disp[stat])
            st = g[~g["fresh"]]
            if len(st):
                d.loc[st.index, "p_ladder"] = M.p_at_least(np.full(len(st), mu), st["need"].to_numpy(float), disp[stat])
        if len(fr) >= 2:
            for i in fr.index:
                rest_ = fr.drop(index=i)
                mu = fit_mu(rest_["need"].to_numpy(float), rest_["mid"].to_numpy(float), disp[stat])
                d.loc[i, "p_loo"] = M.p_at_least(np.array([mu]), np.array([float(fr.loc[i, "need"])]), disp[stat])[0]
    stale = d[d["p_ladder"].notna()]
    loo = d[d["p_loo"].notna()]
    print(f"stale tight rungs priced from fresh ones: {len(stale):,} ({stale.game_id.nunique()} games); "
          f"fresh rungs with leave-one-out: {len(loo):,}")
    for name, q, col in (("stale rungs", stale, "p_ladder"), ("fresh rungs, leave-one-out", loo, "p_loo")):
        if q.empty:
            continue
        print(f"\n{name}: Brier own mid {np.mean((q['mid'] - q['y']) ** 2):.4f} vs ladder fit {np.mean((q[col] - q['y']) ** 2):.4f}")
        for side in ("both", "no"):
            for thr in (0.02, 0.04, 0.06, 0.08):
                b = boot(q, col, thr, side)
                if b["n"]:
                    print(f"  {side:4s} edge>={thr:.2f}: {b['n']:4d} bets ROI {b['roi']:+.1%} [{b['lo']:+.1%} .. {b['hi']:+.1%}]"
                          f" P>0 {b['p_pos']:.0%}")


if __name__ == "__main__":
    main(*sys.argv[1:])
