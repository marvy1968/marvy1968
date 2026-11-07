"""In-game NBA prop models. Each predicts the MEAN of a player's remaining stat at a break; a shared, fitted
overdispersed count distribution turns the mean into P(final >= threshold).

  naive    straight-line pacer: so_far x 48 / minutes of game played
  prop1    Claude Prop 1 (marv/live_prop_engine.project): per-possession production x pace x script
  v2       minutes-first structural model (fitted on train seasons):
             remaining minutes = floor-time share x minutes left x foul / blowout / bench adjustments
                 share = blend(pregame minutes share, tonight's share), weight = elapsed / (elapsed + tau)
             per-minute rate  = blend(pregame per-minute rate, tonight's rate), weight = min_sf / (min_sf + kappa[stat])
             remaining mean   = rate x remaining minutes x (tonight's team pace / usual pace) ^ pace_beta
  ml       gradient-boosted Poisson regression of the remaining stat on the same inputs
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import optimize, stats as sst

STATS = ["pts", "reb", "ast", "fg3"]
LEAGUE_DEFAULTS = {"nba": (48.0, 6), "ncaab": (40.0, 5), "ncaaw": (40.0, 5)}  # regulation minutes, foul-out count


def usable(df: pd.DataFrame) -> pd.DataFrame:
    """Rows with a pregame anchor (5+ earlier games) for players who have played tonight."""
    d = df[(df["pre_n"] >= 5) & (df["pre_min"] >= 8) & (df["min_sf"] > 0)].copy()
    if "league" not in d:
        d["league"] = "nba"
    for col, i in (("game_min", 0), ("foul_out", 1)):
        if col not in d:
            d[col] = d["league"].map(lambda lg: LEAGUE_DEFAULTS[lg][i])
    if "frac" not in d:
        d["frac"] = d["elapsed"] / d["game_min"]
    for s in STATS:
        d[f"{s}_rem"] = d[f"{s}_final"] - d[f"{s}_sf"]
    d["left"] = d["game_min"] - d["elapsed"]
    d["pace_ratio"] = ((d["tposs_sf"] / d["elapsed"] * 36) / d["pre_pace36"]).clip(0.75, 1.3).fillna(1.0)
    return d


# ---------------------------------------------------------------- naive + prop1


def naive(d: pd.DataFrame, s: str) -> np.ndarray:
    return (d[f"{s}_sf"] * d["game_min"] / d["elapsed"] - d[f"{s}_sf"]).to_numpy()


def prop1(d: pd.DataFrame, s: str) -> np.ndarray:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from marv import live_prop_engine as E
    market = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists", "fg3": "player_threes"}[s]
    out = []
    for r in d[[f"{s}_sf", f"pre_{s}", "elapsed", "tposs_sf", "margin", "pf_sf", "min_sf", "league"]].itertuples(index=False):
        st = E.PropState(r[7], market, "", float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                         fouls=float(r[5]), minutes=float(r[6]))
        out.append(E.project(st)["rem_mean"])
    return np.asarray(out)


# ---------------------------------------------------------------- v2 structural


def v2_minutes(d: pd.DataFrame, p: dict) -> np.ndarray:
    pre_share = (d["pre_min"] / d["game_min"]).clip(0, 0.95)
    live_share = (d["min_sf"] / d["elapsed"]).clip(0, 1)
    w = d["elapsed"] / (d["elapsed"] + p["tau"])
    share = (1 - w) * pre_share + w * live_share
    # foul trouble: fouls above the "safe" count for this point of the game cost minutes
    safe = (d["foul_out"] - 1) * d["frac"] + 0.5
    excess = (d["pf_sf"] - safe).clip(lower=0)
    foul = np.exp(-p["foul"] * excess) * np.where(d["pf_sf"] >= d["foul_out"], 0, 1)
    # blowout: starters sit when the margin is large late
    late = d["frac"] / 0.75
    blow = np.exp(-p["blow"] * ((d["margin"].abs() - 12).clip(lower=0) / 10) * late * pre_share)
    bench = np.where(d["on_court"], 1.0, p["bench"])
    return (share * d["left"] * foul * blow * bench).to_numpy()


def v2_mean(d: pd.DataFrame, s: str, p: dict) -> np.ndarray:
    pre_rate = (d[f"pre_{s}"] / d["pre_min"]).clip(lower=0)
    live_rate = (d[f"{s}_sf"] / d["min_sf"].clip(lower=1)).clip(lower=0)
    w = d["min_sf"] / (d["min_sf"] + p[f"kappa_{s}"])
    rate = (1 - w) * pre_rate + w * live_rate
    return np.maximum(rate.to_numpy() * v2_minutes(d, p) * d["pace_ratio"].to_numpy() ** p["pace_beta"], 1e-4)


V2_START = {"tau": 24.0, "foul": 0.3, "blow": 0.4, "bench": 0.95, "pace_beta": 0.5,
            "kappa_pts": 40.0, "kappa_reb": 40.0, "kappa_ast": 40.0, "kappa_fg3": 60.0}


def fit_v2(train: pd.DataFrame) -> dict:
    """Minimise total Poisson deviance of the remaining stat over all four stats (shared minutes model)."""
    keys = list(V2_START)

    def loss(x):
        p = dict(zip(keys, np.exp(x)))
        p["bench"] = min(p["bench"], 1.2)
        tot = 0.0
        for s in STATS:
            mu, y = v2_mean(train, s, p), train[f"{s}_rem"].to_numpy()
            tot += np.mean(mu - y * np.log(mu)) * (1.0 / max(y.mean(), 1e-3))  # scale-free per stat
        return tot

    x0 = np.log(list(V2_START.values()))
    res = optimize.minimize(loss, x0, method="Nelder-Mead", options={"maxiter": 3000, "xatol": 1e-3, "fatol": 1e-7})
    p = dict(zip(keys, np.exp(res.x)))
    p["bench"] = min(p["bench"], 1.2)
    return p


# ---------------------------------------------------------------- ml


ML_FEATS = ["frac", "elapsed", "min_sf", "on_court", "pf_sf", "margin", "pace_ratio", "pre_min", "pre_min_l5", "pre_n",
            "pre_pts", "pre_reb", "pre_ast", "pre_fg3", "pre_fga", "pre_fta", "pts_sf", "reb_sf", "ast_sf", "fg3_sf",
            "fga_sf", "fta_sf", "tov_sf"]


def fit_ml(train: pd.DataFrame, s: str):
    from sklearn.ensemble import HistGradientBoostingRegressor
    X = train[ML_FEATS].astype(float).to_numpy()
    m = HistGradientBoostingRegressor(loss="poisson", max_iter=400, learning_rate=0.05, min_samples_leaf=100,
                                      l2_regularization=1.0, random_state=0)
    m.fit(X, train[f"{s}_rem"].clip(lower=0).to_numpy())
    return m


def ml_mean(m, d: pd.DataFrame) -> np.ndarray:
    return np.maximum(m.predict(d[ML_FEATS].astype(float).to_numpy()), 1e-4)


# ---------------------------------------------------------------- distribution


def fit_dispersion(mu: np.ndarray, y: np.ndarray, bins: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Variance-to-mean ratio phi as a function of the predicted mean (binned on train). var = phi x mu."""
    edges = np.unique(np.quantile(mu, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.digitize(mu, edges[1:-1]), 0, len(edges) - 2)
    centers, phis = [], []
    for b in range(len(edges) - 1):
        m = idx == b
        if m.sum() < 50:
            continue
        centers.append(mu[m].mean())
        resid_var = np.mean((y[m] - mu[m]) ** 2)
        phis.append(max(resid_var / max(mu[m].mean(), 1e-6), 1.02))
    return np.asarray(centers), np.asarray(phis)


def p_at_least(mu: np.ndarray, need: np.ndarray, disp: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """P(remaining >= need) under a negative binomial with var = phi(mu) x mu (need <= 0 -> 1)."""
    phi = np.interp(mu, disp[0], disp[1])
    r = mu / np.maximum(phi - 1, 1e-3)
    pnb = r / (r + mu)
    need = np.asarray(need, float)
    out = np.where(need <= 0, 1.0, sst.nbinom.sf(np.ceil(need) - 1, r, pnb))
    return np.clip(out, 1e-4, 1 - 1e-4)
