"""Blind walk-forward backtest of the power rating on top-N matchups, predicted vs actual win rate.

BLIND = every game is rated using ONLY games dated strictly before it (previous season included, time-decayed, so
week-1 ratings are carry-over). The logistic win-probability scale is fitted on the PREVIOUS season's blind
predictions, never on the season being scored. Nothing from the scored season can leak backwards.

  python3 blind_backtest.py --dir <folder with games.csv [teams.csv]> --season 2026 --top 10
"""
from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

import power_ranks as pr

DEFAULT_SCALE = 7.2      # logistic scale (points) if no prior season exists to fit on
LOOKBACK_DAYS = 420      # previous season + current season so far


def blind_predictions(games: pd.DataFrame, season: int, teams: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per played game in `season`: pre-game rating margin, home field, and national ranks (blind)."""
    g = games.copy(); g["date"] = pd.to_datetime(g["date"])
    g = g[g["hg"].notna() & g["ag"].notna()]
    cur = g[g["season"] == season]
    universe = None
    if teams is not None:
        universe = set(teams[teams["season"] == season]["team"])
    rows = []
    for d, day in cur.groupby("date"):
        train = g[(g["date"] < d) & (g["date"] >= d - pd.Timedelta(days=LOOKBACK_DAYS))]
        if len(train) < 30:
            continue
        r, hfa = pr.massey(train, asof=d - pd.Timedelta(days=1), return_hfa=True)
        pool = r[r.index.isin(universe)] if universe else r
        rank = pool.rank(ascending=False, method="min")
        for x in day.itertuples():
            neu = bool(getattr(x, "neutral", 0)) if "neutral" in day.columns and pd.notna(getattr(x, "neutral", 0)) else False
            rh, ra = float(r.get(x.home, 0.0)), float(r.get(x.away, 0.0))
            rows.append({"season": season, "date": d, "home": x.home, "away": x.away, "hg": x.hg, "ag": x.ag,
                         "neutral": neu, "margin_pred": rh - ra + (0.0 if neu else hfa),
                         "rank_h": rank.get(x.home, np.nan), "rank_a": rank.get(x.away, np.nan),
                         "home_win": 1.0 if x.hg > x.ag else 0.0})
    out = pd.DataFrame(rows)
    return out[out["hg"] != out["ag"]].reset_index(drop=True) if len(out) else out


def fit_scale(pred: pd.DataFrame) -> float:
    """Max-likelihood logistic scale for P(home win)=sigmoid(margin/scale) on a prior season's blind predictions."""
    if pred is None or len(pred) < 200:
        return DEFAULT_SCALE
    m, y = pred["margin_pred"].to_numpy(), pred["home_win"].to_numpy()
    best, bs = None, DEFAULT_SCALE
    for s in np.arange(3.0, 20.01, 0.25):
        p = np.clip(1 / (1 + np.exp(-m / s)), 1e-9, 1 - 1e-9)
        ll = float(np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
        if best is None or ll > best:
            best, bs = ll, float(s)
    return bs


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"),) * 2
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def summarize(p: pd.DataFrame, label: str) -> dict:
    n = len(p)
    if n == 0:
        return {"label": label, "n": 0}
    fav_home = p["p_home"] >= 0.5
    fav_p = np.where(fav_home, p["p_home"], 1 - p["p_home"])
    fav_won = np.where(fav_home, p["home_win"], 1 - p["home_win"])
    y, ph = p["home_win"].to_numpy(), np.clip(p["p_home"].to_numpy(), 1e-9, 1 - 1e-9)
    lo, hi = wilson(int(fav_won.sum()), n)
    return {"label": label, "n": n, "pred_fav_win": float(fav_p.mean()), "actual_fav_win": float(fav_won.mean()),
            "gap": float(fav_won.mean() - fav_p.mean()), "ci95": (lo, hi),
            "pick_acc": float(fav_won.mean()), "brier": float(np.mean((ph - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(ph) + (1 - y) * np.log(1 - ph))),
            "home_baseline_acc": float(max(y.mean(), 1 - y.mean()))}


def run(games: pd.DataFrame, season: int, top: int = 10, teams: pd.DataFrame | None = None) -> dict:
    prev = blind_predictions(games, season - 1, teams)
    scale = fit_scale(prev)
    cur = blind_predictions(games, season, teams)
    if cur.empty:
        return {"error": f"no scoreable games in {season}"}
    cur["p_home"] = 1 / (1 + np.exp(-cur["margin_pred"] / scale))
    both = cur[(cur["rank_h"] <= top) & (cur["rank_a"] <= top)].copy()
    either = cur[(cur["rank_h"] <= top) | (cur["rank_a"] <= top)].copy()
    return {"season": season, "top": top, "scale": scale, "scale_from_prior_season": prev is not None and len(prev) >= 200,
            "top_vs_top": summarize(both, f"top-{top} vs top-{top}"),
            "any_top": summarize(either, f"any game with a top-{top} team"),
            "all_games": summarize(cur, "all games (sanity)"),
            "matchups": both.sort_values("date").reset_index(drop=True)}


def format_report(o: dict) -> str:
    if "error" in o:
        return o["error"]
    L = [f"BLIND BACKTEST  season {o['season']}  top {o['top']}   logistic scale {o['scale']:.2f}"
         f" ({'fitted on prior-season blind predictions' if o['scale_from_prior_season'] else 'DEFAULT - no prior season to fit'})", ""]
    L.append(f"{'set':<34}{'n':>5}{'pred fav%':>10}{'actual fav%':>12}{'gap':>7}{'95% CI (actual)':>18}{'brier':>8}")
    for k in ("top_vs_top", "any_top", "all_games"):
        s = o[k]
        if s["n"] == 0:
            L.append(f"{s['label']:<34}{0:>5}  (no games)")
            continue
        L.append(f"{s['label']:<34}{s['n']:>5}{s['pred_fav_win']:>10.1%}{s['actual_fav_win']:>12.1%}{s['gap']:>+7.1%}"
                 f"{s['ci95'][0]:>10.1%}-{s['ci95'][1]:.1%}{s['brier']:>8.3f}")
    n = o["top_vs_top"]["n"]
    if n and n < 30:
        L += ["", f"WARNING: only {n} top-{o['top']} vs top-{o['top']} games - far too few to judge; the CI above shows how wide it is."]
    L += ["", "Every top-vs-top matchup (pre-game ranks are blind):",
          f"{'date':<11}{'home':<22}{'away':<22}{'rk h/a':>8}{'P(home)':>9}{'pick':>6}{'result':>14}{'ok':>4}"]
    for r in o["matchups"].itertuples():
        pick = "H" if r.p_home >= 0.5 else "A"
        ok = (pick == "H") == (r.home_win == 1.0)
        L.append(f"{r.date:%Y-%m-%d} {r.home:<22}{r.away:<22}{int(r.rank_h):>4}/{int(r.rank_a):<3}{r.p_home:>8.1%}"
                 f"{pick:>6}{int(r.hg):>9}-{int(r.ag):<4}{'Y' if ok else 'N':>4}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    from pathlib import Path
    d = Path(a.dir)
    games = pd.read_csv(d / "games.csv", parse_dates=["date"])
    teams = pd.read_csv(d / "teams.csv") if (d / "teams.csv").exists() else None
    print(format_report(run(games, a.season, a.top, teams)))


if __name__ == "__main__":
    main()
