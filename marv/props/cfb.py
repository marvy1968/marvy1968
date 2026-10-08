"""College football player props (FBS): passing, rushing and receiving yards plus receptions.

Player lines come from free play-by-play (cfbfastR via sportsdataverse). Same projection and Monte Carlo
as the NFL props: player form and usage, the opponent's yards allowed, and the team total implied by the
closing spread and total. Positions aren't in the play-by-play, so usage decides who counts for each
market (passers, ball carriers, targets).
"""

from pathlib import Path

import numpy as np
import pandas as pd

from ..data import cfb_pbp
from .nfl import Market

MARKETS = {
    "pass_yds": Market("player_pass_yds", "passing_yards", (), "attempts", 15, "Passing yards"),
    "rush_yds": Market("player_rush_yds", "rushing_yards", (), "carries", 8, "Rushing yards"),
    "rec_yds": Market("player_reception_yds", "receiving_yards", (), "targets", 4, "Receiving yards"),
    "receptions": Market("player_receptions", "receptions", (), "targets", 4, "Receptions"),
}
FORM = ["passing_yards", "attempts", "completions", "rushing_yards", "carries", "receiving_yards", "receptions", "targets"]
TARGETS = ["passing_yards", "rushing_yards", "receiving_yards", "receptions"]


def load(cache: Path, seasons: list[int], current: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    games, _ = cfb_pbp.load(cache, seasons, current)
    players = pd.concat([t.assign(season=s) for s in seasons
                         if (t := cfb_pbp.player_tables(cache, s, s == current)) is not None], ignore_index=True)
    return games, players


def build_rows(games: pd.DataFrame, players: pd.DataFrame, halflife: float = 4.0) -> pd.DataFrame:
    g = games[["game_id", "date", "season", "home", "away", "neutral", "spread", "total", "home_elo", "away_elo"]]
    p = players.merge(g, on=["game_id", "season"], how="inner")
    p = p[(p["team"] == p["home"]) | (p["team"] == p["away"])].copy()
    for c in FORM:
        p[c] = p[c].fillna(0.0)
    p["player_display_name"] = p["player"]
    p["player_id"] = p["team"] + "|" + p["player"]  # names are unique within a team
    p["position"] = ""
    home = p["team"] == p["home"]
    p["is_home"] = np.where(p["neutral"], 0.5, home.astype(float))
    margin = np.where(home, -p["spread"], p["spread"])  # spread is the home line (negative = home favored)
    p["implied_pts"] = p["total"] / 2 + margin / 2
    p["total_line"] = p["total"]
    p["ctx_elo_gap"] = np.where(home, p["home_elo"] - p["away_elo"], p["away_elo"] - p["home_elo"])
    p = p.sort_values(["player_id", "date"]).reset_index(drop=True)
    gp = p.groupby("player_id", sort=False)
    for c in FORM:
        prev = gp[c].shift()
        p[f"ewm_{c}"] = prev.groupby(p["player_id"]).transform(lambda s: s.ewm(halflife=halflife, min_periods=1).mean())
        p[f"l3_{c}"] = prev.groupby(p["player_id"]).transform(lambda s: s.rolling(3, min_periods=1).mean())
    for c in TARGETS:
        prev = p.groupby(["player_id", "season"])[c].shift()
        p[f"szn_{c}"] = prev.groupby([p["player_id"], p["season"]]).transform(lambda s: s.expanding().mean())
    p["games_before"] = gp.cumcount()
    allow = p.groupby(["game_id", "date", "season", "opponent_team"], as_index=False)[TARGETS].sum().sort_values("date")
    for c in TARGETS:
        prev = allow.groupby("opponent_team")[c].shift()
        allow[f"def_{c}"] = prev.groupby(allow["opponent_team"]).transform(lambda s: s.ewm(halflife=6, min_periods=1).mean())
        league = allow.groupby("season")[c].transform(lambda s: s.shift().expanding().mean())
        allow[f"def_{c}"] = allow[f"def_{c}"] / league.replace(0, np.nan)
    p = p.merge(allow[["game_id", "opponent_team", *[f"def_{c}" for c in TARGETS]]], on=["game_id", "opponent_team"], how="left")
    p["week"] = ((p["date"] - p.groupby("season")["date"].transform("min")).dt.days // 7 + 1).astype(int)
    return p
