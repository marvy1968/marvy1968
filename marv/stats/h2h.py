"""Head-to-head category matrix ("Max Pick" method) with a last-3-games trend and Monte Carlo.

For every stat, the team with the better season-to-date number gets a point: e.g. opponent yards per
play allowed, Georgia 4.4 vs Alabama 5.0, so Georgia gets the point. Offensive stats and the same stats
allowed on defense are both compared, then the same tally is repeated on each team's last 3 games (the
trend). Which way is "better" for each stat (fewer turnovers, more yards) is learned from earlier
seasons, not guessed. The point difference becomes a projected margin, season scoring averages give
the total, and the sport's shared Monte Carlo turns that into win, cover and over/under probabilities.

Everything is point-in-time: a game only sees games played before it.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import StatsModule


def team_features(tg: pd.DataFrame, stat_cols: list[str], trend_games: int = 3, prior_games: float = 0.0,
                  count_only: pd.Series | None = None) -> pd.DataFrame:
    """Season-to-date and last-N averages of every stat and every stat allowed, before each game.

    prior_games > 0 blends in last season's full average as if it were that many extra games, so
    early weeks aren't decided by one or two results. The last-N trend runs across seasons.
    count_only (bool per tg row) limits which games feed the averages, e.g. only games against top-30 teams."""
    stat_cols = [c for c in dict.fromkeys(["points", *stat_cols]) if c in tg]
    opp = tg[["game_id", "team", *stat_cols]].rename(columns={"team": "opp", **{c: f"alw_{c}" for c in stat_cols}})
    x = tg[["game_id", "date", "season", "team", "opp", "home", *stat_cols]].assign(
        _use=True if count_only is None else count_only.to_numpy()).merge(opp, on=["game_id", "opp"], how="left")
    x = x[x["points"].notna()].sort_values(["date", "game_id"]).reset_index(drop=True)
    cols = stat_cols + [f"alw_{c}" for c in stat_cols]
    keys = [x["team"], x["season"]]
    vals = x[cols].astype(float)
    vals.loc[~x["_use"].astype(bool).to_numpy()] = np.nan
    have = vals.notna().astype(float)
    total = vals.fillna(0).groupby(keys).cumsum() - vals.fillna(0)
    count = have.groupby(keys).cumsum() - have
    if prior_games > 0:
        full = vals.groupby(keys).mean()
        full.index = full.index.set_levels(full.index.levels[1] + 1, level=1)  # last season -> this season
        prior = full.reindex(pd.MultiIndex.from_arrays(keys)).to_numpy()
        ok = ~np.isnan(prior)
        total = total + np.where(ok, prior * prior_games, 0)
        count = count + np.where(ok, prior_games, 0)
    season = total / count.replace(0, np.nan)
    shifted = vals.groupby(x["team"]).shift()
    last = shifted.groupby(x["team"]).rolling(trend_games, min_periods=2).mean().reset_index(level=0, drop=True)
    out = pd.concat([x[["game_id", "team", "season"]], season.add_prefix("s_"), last.sort_index().add_prefix("t_")], axis=1)
    out["n"] = x.groupby(keys).cumcount()
    return out


def matchups(games: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
    """One row per game with both teams' features side by side (h_* home, a_* away)."""
    f = feats.drop(columns="season").set_index(["game_id", "team"])
    h = f.reindex(list(zip(games["game_id"], games["home"]))).add_prefix("h_").reset_index(drop=True)
    a = f.reindex(list(zip(games["game_id"], games["away"]))).add_prefix("a_").reset_index(drop=True)
    return pd.concat([games.reset_index(drop=True), h, a], axis=1)


def categories(m: pd.DataFrame) -> list[str]:
    return [c[4:] for c in m.columns if c.startswith("h_s_")]


@dataclass
class H2HModel:
    direction: dict  # stat -> +1 if higher is better, -1 if lower is better
    coef: np.ndarray  # margin = coef . [home_flag, season_net, trend_net]
    total_coef: np.ndarray  # total = coef . [1, scoring_average_total]
    cats: list


def tallies(m: pd.DataFrame, direction: dict) -> tuple[np.ndarray, np.ndarray]:
    """Net category points for the home team (season, trend): +1 per category won, -1 per category lost."""
    season = np.zeros(len(m))
    trend = np.zeros(len(m))
    for c, d in direction.items():
        if d:
            season += d * np.sign((m[f"h_s_{c}"] - m[f"a_s_{c}"]).fillna(0).to_numpy())
            trend += d * np.sign((m[f"h_t_{c}"] - m[f"a_t_{c}"]).fillna(0).to_numpy())
    return season, trend


def scoring_total(m: pd.DataFrame) -> np.ndarray:
    """Each team's points per game blended with what the opponent allows."""
    return ((m["h_s_points"] + m["a_s_alw_points"]) / 2 + (m["a_s_points"] + m["h_s_alw_points"]) / 2).to_numpy()


def home_flag(m: pd.DataFrame) -> np.ndarray:
    return np.where(m["neutral"].fillna(False).astype(bool), 0.0, 1.0)


def fit(m: pd.DataFrame) -> H2HModel:
    """Learn each stat's direction and the tally -> margin / total mapping from finished games."""
    m = m[m["home_points"].notna()]
    margin = (m["home_points"] - m["away_points"]).to_numpy()
    direction = {}
    for c in categories(m):
        diff = np.sign((m[f"h_s_{c}"] - m[f"a_s_{c}"]).to_numpy())
        ok = ~np.isnan(diff)
        agree = np.mean(diff[ok] * np.sign(margin[ok])) if ok.any() else 0.0
        direction[c] = int(np.sign(agree)) if abs(agree) > 0.02 else 0  # skip stats with no signal at all
    s, t = tallies(m, direction)
    X = np.column_stack([home_flag(m), s, t])
    coef = np.linalg.lstsq(X, margin, rcond=None)[0]
    tot = scoring_total(m)
    ok = ~np.isnan(tot)
    total_coef = np.linalg.lstsq(np.column_stack([np.ones(ok.sum()), tot[ok]]),
                                 (m["home_points"] + m["away_points"]).to_numpy()[ok], rcond=None)[0]
    return H2HModel(direction, coef, total_coef, categories(m))


def predict(m: pd.DataFrame, model: H2HModel, module: StatsModule, n: int = 2000, seed: int = 0) -> pd.DataFrame:
    """Tally -> projected scores -> Monte Carlo probabilities."""
    rng = np.random.default_rng(seed)
    m = m.copy()
    m["season_net"], m["trend_net"] = tallies(m, model.direction)
    m["h2h_margin"] = np.column_stack([home_flag(m), m["season_net"], m["trend_net"]]) @ model.coef
    m["h2h_total"] = model.total_coef[0] + model.total_coef[1] * scoring_total(m)
    p_home, p_cover, p_over = [], [], []
    for mg, tot, spread, line in zip(m["h2h_margin"], m["h2h_total"], m["spread"], m["total"]):
        if np.isnan(tot):
            p_home.append(np.nan); p_cover.append(np.nan); p_over.append(np.nan)
            continue
        sim = module.simulate(max(tot / 2 + mg / 2, 0.5), max(tot / 2 - mg / 2, 0.5), n, rng)
        p_home.append(sim.home_win())
        p_cover.append(_beat(sim.margin + spread, 0) if pd.notna(spread) else np.nan)
        p_over.append(_beat(sim.total, line) if pd.notna(line) else np.nan)
    m["p_home"], m["p_cover"], m["p_over"] = p_home, p_cover, p_over
    return m


def _beat(values: np.ndarray, line: float) -> float:
    decided = values != line
    return float((values[decided] > line).mean()) if decided.any() else 0.5


def walk_forward(module: StatsModule, games: pd.DataFrame, tg: pd.DataFrame, test_seasons: list[int],
                 min_games: int = 3, n: int = 2000, prior_games: float = 0.0, trend: bool = True,
                 count_only: pd.Series | None = None) -> pd.DataFrame:
    """Each test season is predicted by a model fit only on the seasons before it."""
    feats = team_features(tg, module.stat_columns(tg), prior_games=prior_games, count_only=count_only)
    if not trend:
        feats[[c for c in feats if c.startswith("t_")]] = np.nan
    m = matchups(games, feats)
    m = m[(m["h_n"] >= min_games) & (m["a_n"] >= min_games)]
    out = []
    for season in test_seasons:
        train = m[m["season"] < season]
        test = m[m["season"] == season]
        if train.empty or test.empty:
            continue
        out.append(predict(test, fit(train), module, n=n, seed=season))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def grade(df: pd.DataFrame, p_col: str, outcome: np.ndarray, min_edge: float, price: float = -110) -> dict:
    """Bets on whichever side has probability >= 0.5 + min_edge (pushes excluded)."""
    p = df[p_col].to_numpy(dtype=float)
    pick = np.where(p >= 0.5, 1.0, 0.0)
    conf = np.maximum(p, 1 - p)
    bet = (conf >= 0.5 + min_edge) & ~np.isnan(p) & ~np.isnan(outcome) & (outcome != 0.5)
    wins = (pick[bet] == outcome[bet]).sum()
    n = int(bet.sum())
    win_pay = 100 / -price if price < 0 else price / 100
    roi = (wins * win_pay - (n - wins)) / n if n else np.nan
    return {"n": n, "win_rate": wins / n if n else np.nan, "roi": roi}


def outcomes(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """1 = home win / home cover / over, 0 = the other side, 0.5 = push, nan = no line."""
    margin = (df["home_points"] - df["away_points"]).to_numpy(dtype=float)
    ats = margin + df["spread"].to_numpy(dtype=float)
    tot = (df["home_points"] + df["away_points"]).to_numpy(dtype=float) - df["total"].to_numpy(dtype=float)
    f = lambda v: np.where(np.isnan(v), np.nan, np.where(v > 0, 1.0, np.where(v < 0, 0.0, 0.5)))
    return {"ml": f(margin), "ats": f(ats), "ou": f(tot)}


def report(df: pd.DataFrame, held_out_from: int) -> str:
    """Win rates on the seasons from `held_out_from` on (moneyline by confidence, spread and total by edge)."""
    test = df[df["season"] >= held_out_from]
    if test.empty:
        return "No held-out games."
    o = outcomes(test)
    p = test["p_home"].to_numpy(dtype=float)
    conf = np.maximum(p, 1 - p)
    right = np.where(p >= 0.5, 1.0, 0.0) == o["ml"]
    seasons = sorted(test["season"].unique().tolist())
    lines = [f"Max Pick head-to-head, held-out seasons {seasons[0]}-{seasons[-1]} ({len(test)} games)",
             f"Moneyline, every game: {right.mean():.1%}"]
    for lo in (0.7, 0.8, 0.85, 0.9):
        mask = conf >= lo
        if mask.any():
            lines.append(f"Moneyline, model >= {lo:.0%}: {right[mask].mean():.1%} ({int(mask.sum())} picks)")
    for label, col, market in (("Spread", "p_cover", "ats"), ("Over/under", "p_over", "ou")):
        if test[col].notna().any():
            for e in (0.0, 0.05, 0.10):
                g = grade(test, col, o[market], e)
                if g["n"]:
                    lines.append(f"{label}, edge >= {e:.0%}: {g['win_rate']:.1%} ({g['n']} picks, ROI {g['roi']:+.1%} at -110)")
        else:
            lines.append(f"{label}: no historical lines in this data source")
    return "\n".join(lines)
