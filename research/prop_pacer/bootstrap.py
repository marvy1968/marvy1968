"""Uncertainty of the Kalshi paper-bet ROI: resample whole GAMES (bets in one game are not independent).

usage: python research/prop_pacer/bootstrap.py MARKET_ROWS.parquet
MARKET_ROWS is the per-contract table evaluate.py writes when PACER_SAVE_MARKET=path is set.
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __import__("os").path.dirname(__file__))
from evaluate import fee  # noqa: E402


def bets(d: pd.DataFrame, col: str, thr: float) -> pd.DataFrame:
    p, ya, yb, y = d[col].to_numpy(), d["yes_ask"].to_numpy(), d["yes_bid"].to_numpy(), d["y"].to_numpy()
    na = 1 - yb
    ok = (ya - yb <= 0.10) & (yb >= 0.02) & (ya <= 0.98)
    e_yes, e_no = p - ya - fee(ya), (1 - p) - na - fee(na)
    by = ok & (e_yes >= thr) & (e_yes >= e_no) & (ya >= 0.05)
    bn = ok & (e_no >= thr) & (e_no > e_yes) & (na >= 0.05)
    cost = np.where(by, ya + fee(ya), na + fee(na))
    win = np.where(by, y, 1 - y)
    out = d[by | bn].copy()
    out["cost"], out["pnl"], out["side"] = cost[by | bn], (win - cost)[by | bn], np.where(by, "yes", "no")[by | bn]
    return out


def main(path: str) -> None:
    d = pd.read_parquet(path)
    rng = np.random.default_rng(0)
    games = d["game_id"].unique()
    print(f"{len(d):,} contracts, {len(games)} games, {d['date'].min()} to {d['date'].max()} (whole sample)")
    for col in ("p_naive", "p_prop1", "p_v2", "p_ml"):
        for thr in (0.04, 0.06, 0.08):
            b = bets(d, col, thr)
            if b.empty:
                continue
            g = b.groupby("game_id")[["pnl", "cost"]].sum().reindex(games, fill_value=0)
            rois = []
            for _ in range(2000):
                s = g.iloc[rng.integers(0, len(g), len(g))].sum()
                rois.append(s["pnl"] / s["cost"] if s["cost"] else 0)
            lo, hi = np.percentile(rois, [5, 95])
            roi = b["pnl"].sum() / b["cost"].sum()
            print(f"{col[2:]:6s} edge>={thr:.2f}: {len(b):4d} bets ({(b.side == 'yes').mean():.0%} YES) ROI {roi:+.1%} "
                  f"90% range {lo:+.1%} .. {hi:+.1%}  P(ROI>0) {np.mean(np.array(rois) > 0):.0%}")
    b = bets(d, "p_v2", 0.06)
    print("\nminutes model, edge >= 6c, by slice (whole sample):")
    for key in ("k", "stat", "side"):
        t = b.groupby(key).agg(n=("pnl", "size"), pnl=("pnl", "sum"), cost=("cost", "sum"))
        t["roi"] = t["pnl"] / t["cost"]
        print(t.round(3).to_string(), "\n")


if __name__ == "__main__":
    main(sys.argv[1])
