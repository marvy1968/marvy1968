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
    if all(f"alw_{c}" in tg for c in stat_cols):  # allowed stats already supplied (e.g. opponent-adjusted)
        x = tg[["game_id", "date", "season", "team", "opp", "home", *stat_cols, *[f"alw_{c}" for c in stat_cols]]].assign(
            _use=True if count_only is None else count_only.to_numpy())
    else:
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


def opponent_adjust(tg: pd.DataFrame, stat_cols: list[str], passes: int = 1, prior_games: float = 0.0) -> pd.DataFrame:
    """Recalculate every head-to-head data point for the opponents it came against.

    A team's offensive number in a game is moved by how much that opponent's defense allowed versus the
    league (gaining 6.0 yards per play against a defense that allows 6.5 counts as 5.5 vs average), and
    each stat allowed is moved by how good the opposing offense was. Opponent and league numbers are
    pre-game only. passes > 1 repeats it with already-adjusted numbers (strength of schedule of the
    opponents' opponents). Returns tg with adjusted stat columns plus alw_* columns."""
    stat_cols = [c for c in dict.fromkeys(["points", *stat_cols]) if c in tg]
    tg = tg.reset_index(drop=True)
    raw_off = tg[stat_cols].astype(float)
    raw_alw = tg[["game_id", "opp"]].merge(
        tg[["game_id", "team", *stat_cols]].rename(columns={"team": "opp"}), on=["game_id", "opp"], how="left")[stat_cols].astype(float)
    # League average of each stat over the season's earlier dates (last season's average before then).
    day = tg.groupby(["season", "date"])[stat_cols].agg(["sum", "count"])
    sums = day.xs("sum", axis=1, level=1).groupby(level=0).cumsum() - day.xs("sum", axis=1, level=1)
    cnts = day.xs("count", axis=1, level=1).groupby(level=0).cumsum() - day.xs("count", axis=1, level=1)
    league = sums / cnts.replace(0, np.nan)
    season_mean = tg.groupby("season")[stat_cols].mean()
    season_mean.index = season_mean.index + 1
    league = league.fillna(season_mean.reindex(league.index.get_level_values(0)).set_axis(league.index))
    lg = league.reindex(pd.MultiIndex.from_frame(tg[["season", "date"]])).to_numpy()
    off, alw = raw_off.copy(), raw_alw.copy()
    for _ in range(passes):
        cur = tg[["game_id", "date", "season", "team", "opp", "home"]].copy()
        cur[stat_cols] = off.to_numpy()
        cur[[f"alw_{c}" for c in stat_cols]] = alw.to_numpy()
        f = team_features(cur, stat_cols, prior_games=prior_games).set_index(["game_id", "team"])
        o = f.reindex(list(zip(tg["game_id"], tg["opp"])))
        opp_alw = o[[f"s_alw_{c}" for c in stat_cols]].to_numpy()
        opp_off = o[[f"s_{c}" for c in stat_cols]].to_numpy()
        off = raw_off - np.nan_to_num(opp_alw - lg)
        alw = raw_alw - np.nan_to_num(opp_off - lg)
    out = tg.copy()
    out[stat_cols] = off.to_numpy()
    out[[f"alw_{c}" for c in stat_cols]] = alw.to_numpy()
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
    coef: np.ndarray  # margin = coef . [home_flag, season_net, trend_net]  (equal mode)
    total_coef: np.ndarray  # total = coef . [1, scoring_average_total]
    cats: list
    mode: str = "equal"  # equal: 1 point per stat · weighted: learned weight per stat · magnitude: weighted size of each edge
    weights: object = None  # fitted ridge model for the weighted modes
    scale: dict = None  # per-stat spread of differences (magnitude mode)


def category_matrix(m: pd.DataFrame, cats: list, mode: str, scale: dict | None = None) -> np.ndarray:
    """One column per stat (season and last-3 trend) plus home: who wins it (sign) or by how much (scaled)."""
    cols = [home_flag(m)]
    for prefix in ("s", "t"):
        for c in cats:
            diff = (m[f"h_{prefix}_{c}"] - m[f"a_{prefix}_{c}"]).to_numpy(float)
            if mode == "magnitude":
                diff = np.clip(diff / (scale or {}).get(f"{prefix}_{c}", 1.0), -3, 3)
            else:
                diff = np.sign(diff)
            cols.append(np.nan_to_num(diff))
    return np.column_stack(cols)


def stat_weights(model: "H2HModel") -> pd.Series:
    """Learned weight of each stat (season columns), largest first; in points of margin per stat won."""
    if model.weights is None:
        return pd.Series({c: float(model.direction.get(c, 0) * model.coef[1]) for c in model.cats})
    coef = model.weights.coef_[1:1 + len(model.cats)]
    return pd.Series(coef, index=model.cats).sort_values(key=abs, ascending=False)


def breakdown(m: pd.DataFrame, model: "H2HModel", i: int = 0, top: int = 10) -> pd.DataFrame:
    """The recalculated head-to-head data points for one game (row i of a matchups frame): each stat's
    home and away numbers and the weighted points it contributes to the home margin (weight x edge),
    largest first. In equal mode every stat won is worth the same coef."""
    row = m.iloc[[i]]
    rows = []
    if model.weights is not None:
        x = category_matrix(row, model.cats, model.mode, model.scale)[0]
        coef = model.weights.coef_
        rows.append({"stat": "home field", "home": None, "away": None, "points": float(coef[0] * x[0])})
        k = 1
        for prefix, label in (("s", "season"), ("t", "last 3")):
            for c in model.cats:
                rows.append({"stat": f"{c} ({label})", "home": row[f"h_{prefix}_{c}"].iloc[0],
                             "away": row[f"a_{prefix}_{c}"].iloc[0], "points": float(coef[k] * x[k])})
                k += 1
    else:
        for prefix, label, w in (("s", "season", model.coef[1]), ("t", "last 3", model.coef[2])):
            for c, d in model.direction.items():
                h, a = row[f"h_{prefix}_{c}"].iloc[0], row[f"a_{prefix}_{c}"].iloc[0]
                rows.append({"stat": f"{c} ({label})", "home": h, "away": a,
                             "points": float(w * d * np.sign(np.nan_to_num(h - a)))})
    out = pd.DataFrame(rows)
    return out.reindex(out["points"].abs().sort_values(ascending=False).index).head(top).reset_index(drop=True)


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


def fit(m: pd.DataFrame, mode: str = "equal") -> H2HModel:
    """Learn each stat's direction and the tally -> margin / total mapping from finished games.
    mode="weighted" learns a weight per stat instead of 1 point each; mode="magnitude" also uses how big
    each edge is. Both use ridge regression so weak or redundant stats shrink toward zero."""
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
    model = H2HModel(direction, coef, total_coef, categories(m), mode=mode)
    if mode != "equal":
        from sklearn.linear_model import RidgeCV
        cats = categories(m)
        if mode == "magnitude":
            model.scale = {f"{p}_{c}": float(np.nanstd(m[f"h_{p}_{c}"] - m[f"a_{p}_{c}"]) or 1.0)
                           for p in ("s", "t") for c in cats}
        X = category_matrix(m, cats, mode, model.scale)
        model.weights = RidgeCV(alphas=np.logspace(-1, 5, 40)).fit(X, margin)
    return model


def predict(m: pd.DataFrame, model: H2HModel, module: StatsModule, n: int = 2000, seed: int = 0) -> pd.DataFrame:
    """Tally -> projected scores -> Monte Carlo probabilities."""
    rng = np.random.default_rng(seed)
    m = m.copy()
    m["season_net"], m["trend_net"] = tallies(m, model.direction)
    if model.weights is not None:
        m["h2h_margin"] = model.weights.predict(category_matrix(m, model.cats, model.mode, model.scale))
    else:
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
                 count_only: pd.Series | None = None, mode: str = "equal", train_years: int = 6,
                 models: dict | None = None, adjust: int = 0) -> pd.DataFrame:
    """Each test season is predicted by a model fit only on the seasons before it (the last `train_years`).
    Pass a dict as `models` to collect the fitted model per season. adjust > 0 first recalculates every
    data point for opponent strength (that many passes, see opponent_adjust)."""
    cols = module.stat_columns(tg)
    if adjust:
        tg = opponent_adjust(tg, cols, passes=adjust, prior_games=prior_games)
    feats = team_features(tg, cols, prior_games=prior_games, count_only=count_only)
    if not trend:
        feats[[c for c in feats if c.startswith("t_")]] = np.nan
    m = matchups(games, feats)
    m = m[(m["h_n"] >= min_games) & (m["a_n"] >= min_games)]
    out = []
    for season in test_seasons:
        train = m[(m["season"] < season) & (m["season"] >= season - train_years)]
        test = m[m["season"] == season]
        if train.empty or test.empty:
            continue
        model = fit(train, mode)
        if models is not None:
            models[season] = model
        out.append(predict(test, model, module, n=n, seed=season))
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
