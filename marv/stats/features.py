"""Pre-game offense/defense features from team box scores (shared by every sport).

Input is a long "team-games" frame, one row per team per game:
    game_id, date, season, team, opp, home (1 home / 0 away / 0.5 neutral), points, <stat columns...>

For every stat the builder tracks two exponentially weighted averages per team, using only
games played *before* the one being predicted:
    off_<stat>  what the team produces (its offense)
    alw_<stat>  what its opponents produce against it (its defense: stats allowed)

A model row for "team T vs opponent O" then holds T's offense, O's defense, T's defense,
O's offense, venue, rest and sample sizes, and the target is T's points.
"""

import numpy as np
import pandas as pd


def _ewm_shifted(df: pd.DataFrame, cols: list[str], halflife: float) -> pd.DataFrame:
    """Per-team EWMA of cols using only earlier rows (df must be sorted by team, date)."""
    shifted = df.groupby("team", sort=False)[cols].shift(1)
    shifted = pd.concat([shifted, df[["team"]]], axis=1)
    out = shifted.groupby("team", sort=False)[cols].transform(
        lambda s: s.ewm(halflife=halflife, ignore_na=True, min_periods=1).mean())
    return out


def build_features(tg: pd.DataFrame, stat_cols: list[str], halflife: float,
                   extra_cols: list[str] | None = None) -> pd.DataFrame:
    """Return one model row per team-game with pre-game features and the target `points`."""
    tg = tg.copy()
    tg["date"] = pd.to_datetime(tg["date"])
    stat_cols = [c for c in dict.fromkeys(["points", *stat_cols]) if c in tg.columns]
    tg[stat_cols] = tg[stat_cols].apply(pd.to_numeric, errors="coerce").astype(float)

    # Opponent's line in the same game = what this team allowed.
    opp = tg[["game_id", "team", *stat_cols]].rename(columns={"team": "opp", **{c: f"o_{c}" for c in stat_cols}})
    tg = tg.merge(opp, on=["game_id", "opp"], how="left").copy()
    tg = tg.sort_values(["team", "date", "game_id"], kind="stable").reset_index(drop=True)

    off = _ewm_shifted(tg, stat_cols, halflife).add_prefix("off_")
    allowed = pd.concat([tg[["team"]], tg[[f"o_{c}" for c in stat_cols]].set_axis(stat_cols, axis=1)], axis=1)
    alw = _ewm_shifted(allowed, stat_cols, halflife).add_prefix("alw_")
    feats = pd.concat([tg[["game_id", "date", "season", "team", "opp", "home", "points"]], off, alw], axis=1)
    feats["gp"] = tg.groupby(["team", "season"]).cumcount()  # games already played this season
    feats["gp_all"] = tg.groupby("team").cumcount()
    prev = tg.groupby("team")["date"].shift(1)
    feats["rest"] = ((tg["date"] - prev).dt.days).clip(upper=10).fillna(10)
    if extra_cols:  # already pre-game values computed by the sport module (e.g. starting pitcher form)
        feats = pd.concat([feats, tg[extra_cols].reset_index(drop=True)], axis=1)

    # Attach the opponent's pre-game profile for the same game.
    side_cols = [c for c in feats.columns if c.startswith(("off_", "alw_"))] + ["gp", "rest", *(extra_cols or [])]
    opp_side = feats[["game_id", "team", *side_cols]].rename(
        columns={"team": "opp", **{c: f"opp_{c}" for c in side_cols}})
    model = feats.merge(opp_side, on=["game_id", "opp"], how="left")

    # Matchup terms the stat formula can read directly: my offense vs their defense, etc.
    matchup = {}
    for c in stat_cols:
        matchup[f"mx_{c}"] = model[f"off_{c}"] + model[f"opp_alw_{c}"]
        matchup[f"mxd_{c}"] = model[f"alw_{c}"] + model[f"opp_off_{c}"]
    model = pd.concat([model, pd.DataFrame(matchup)], axis=1)
    return model.sort_values(["date", "game_id", "home"], ascending=[True, True, False]).reset_index(drop=True)


def feature_columns(model: pd.DataFrame) -> list[str]:
    skip = {"game_id", "date", "season", "team", "opp", "points"}
    return [c for c in model.columns if c not in skip and model[c].dtype != object]


def to_matrix(model: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return model[cols].to_numpy(dtype=float)
