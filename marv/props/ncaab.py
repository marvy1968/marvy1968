"""NCAA men's basketball player props: points, rebounds, assists and made threes.

Projection inputs (all known before tip-off):
  * the player's form: weighted recent average, season average, last 3 games, minutes and shot volume
  * the opponent: what that defense has allowed to the player's position group, relative to the league
  * the game: both teams' pace (possessions) and scoring, home/away
Same gradient-boosted model and Monte Carlo of past misses as the NFL props.
Data: ESPN player and team box scores via sportsdataverse (free), Division I teams only.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..stats.base import fetch
from .nfl import Market

log = logging.getLogger(__name__)
BOX_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
           "espn_mens_college_basketball_player_boxscores/player_box_{season}.parquet")

MARKETS = {
    "points": Market("player_points", "points", (), "minutes", 20, "Points"),
    "rebounds": Market("player_rebounds", "rebounds", (), "minutes", 20, "Rebounds"),
    "assists": Market("player_assists", "assists", (), "minutes", 20, "Assists"),
    "threes": Market("player_threes", "three_point_field_goals_made", (), "minutes", 20, "Made threes"),
}
FORM = ["points", "rebounds", "assists", "three_point_field_goals_made", "three_point_field_goals_attempted",
        "field_goals_attempted", "free_throws_attempted", "offensive_rebounds", "defensive_rebounds", "turnovers",
        "minutes", "starter"]
TARGETS = ["points", "rebounds", "assists", "three_point_field_goals_made"]


def load_box(cache: Path, seasons: list[int], current: int | None = None) -> pd.DataFrame:
    frames = []
    for season in seasons:
        path = fetch(BOX_URL.format(season=season), cache / f"mbb_player_box_{season}.parquet",
                     2 if season == current else None)
        if path:
            frames.append(pd.read_parquet(path))
    b = pd.concat(frames, ignore_index=True)
    b = b[b["season_type"].isin([2, 3]) & ~b["did_not_play"].fillna(False).astype(bool) & b["minutes"].notna()]
    b = b.rename(columns={"athlete_id": "player_id", "athlete_display_name": "player_display_name",
                          "team_display_name": "team", "opponent_team_display_name": "opponent_team"})
    b["game_id"] = b["game_id"].astype(str)
    b["date"] = pd.to_datetime(b["game_date"])
    b["position"] = b["athlete_position_abbreviation"].fillna("").str[:1].map(
        lambda x: x if x in ("G", "F", "C") else "U")
    b["starter"] = b["starter"].astype(float)
    b["is_home"] = (b["home_away"] == "home").astype(float)
    return b


def team_context(b: pd.DataFrame) -> pd.DataFrame:
    """Pre-game pace and scoring for each team-game, from the teams' earlier games."""
    t = b.groupby(["game_id", "team", "opponent_team", "date", "season"], as_index=False).agg(
        fga=("field_goals_attempted", "sum"), fta=("free_throws_attempted", "sum"),
        orb=("offensive_rebounds", "sum"), tov=("turnovers", "sum"), pts=("team_score", "first"),
        allowed=("opponent_team_score", "first"))
    t["poss"] = t["fga"] + 0.44 * t["fta"] - t["orb"] + t["tov"]
    t = t.sort_values("date")
    for c in ("poss", "pts", "allowed"):
        prev = t.groupby("team")[c].shift()
        t[f"e_{c}"] = prev.groupby(t["team"]).transform(lambda s: s.ewm(halflife=8, min_periods=1).mean())
    opp = t[["game_id", "team", "e_poss", "e_pts", "e_allowed"]].rename(
        columns={"team": "opponent_team", "e_poss": "o_poss", "e_pts": "o_pts", "e_allowed": "o_allowed"})
    t = t.merge(opp, on=["game_id", "opponent_team"], how="left")
    t["ctx_pace"] = (t["e_poss"] + t["o_poss"]) / 2
    t["ctx_team_pts"] = (t["e_pts"] + t["o_allowed"]) / 2
    t["ctx_opp_pts"] = (t["o_pts"] + t["e_allowed"]) / 2
    return t[["game_id", "team", "ctx_pace", "ctx_team_pts", "ctx_opp_pts"]]


def build_rows(b: pd.DataFrame, halflife: float = 5.0) -> pd.DataFrame:
    # Division I only: D1 teams play 20+ games a season in this data, visiting small schools appear once or twice.
    games_per = b.groupby(["season", "team"])["game_id"].nunique()
    d1 = set(games_per[games_per >= 20].index)
    b = b[[(s, t) in d1 and (s, o) in d1 for s, t, o in zip(b["season"], b["team"], b["opponent_team"])]]
    p = b.sort_values(["player_id", "date"]).reset_index(drop=True)
    g = p.groupby("player_id", sort=False)
    for c in FORM:
        prev = g[c].shift()
        p[f"ewm_{c}"] = prev.groupby(p["player_id"]).transform(lambda s: s.ewm(halflife=halflife, min_periods=1).mean())
        p[f"l3_{c}"] = prev.groupby(p["player_id"]).transform(lambda s: s.rolling(3, min_periods=1).mean())
    for c in TARGETS:
        prev = p.groupby(["player_id", "season"])[c].shift()
        p[f"szn_{c}"] = prev.groupby([p["player_id"], p["season"]]).transform(lambda s: s.expanding().mean())
    p["games_before"] = g.cumcount()

    allow = p.groupby(["game_id", "date", "season", "opponent_team", "position"], as_index=False)[TARGETS].sum()
    allow = allow.sort_values("date")
    for c in TARGETS:
        prev = allow.groupby(["opponent_team", "position"])[c].shift()
        allow[f"def_{c}"] = prev.groupby([allow["opponent_team"], allow["position"]]).transform(
            lambda s: s.ewm(halflife=8, min_periods=1).mean())
        league = allow.groupby(["season", "position"])[c].transform(lambda s: s.shift().expanding().mean())
        allow[f"def_{c}"] = allow[f"def_{c}"] / league.replace(0, np.nan)
    p = p.merge(allow[["game_id", "opponent_team", "position", *[f"def_{c}" for c in TARGETS]]],
                on=["game_id", "opponent_team", "position"], how="left")
    p = p.merge(team_context(b), on=["game_id", "team"], how="left")
    p["ctx_home"] = p["is_home"]
    p["week"] = p["date"].dt.isocalendar().week.astype(int)
    return p
