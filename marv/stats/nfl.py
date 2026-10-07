"""NFL stats module: nflverse weekly team stats (every offensive, defensive and special-teams column)."""

from pathlib import Path

import numpy as np
import pandas as pd

from ..data.nflverse import URL as GAMES_URL
from ..data.teams import NFL_TEAMS
from ..ratings import RatingParams
from ..sims import football
from .base import StatsModule, fetch
from .experts import ExpertConfig

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv"
DROP = {"season", "week", "team", "season_type", "game_id", "opponent_team"}


def _sim(h, a, n, rng):
    return football.simulate_game(h, a, football.NFL, n=n, rng=rng)


class NFLStats(StatsModule):
    def load(self, cache: Path, seasons: list[int], current: int | None = None):
        gpath = fetch(GAMES_URL, cache / "nflverse_games.csv", max_age_hours=3)
        g = pd.read_csv(gpath, low_memory=False)
        g = g[g["season"].isin(seasons)]
        games = pd.DataFrame({
            "game_id": g["game_id"],
            "date": pd.to_datetime(g["gameday"]),
            "season": g["season"],
            "home": g["home_team"].map(lambda t: NFL_TEAMS.get(t, t)),
            "away": g["away_team"].map(lambda t: NFL_TEAMS.get(t, t)),
            "neutral": g["location"].eq("Neutral"),
            "home_points": g["home_score"], "away_points": g["away_score"],
            "spread": -g["spread_line"], "total": g["total_line"],
            "home_ml": g["home_moneyline"], "away_ml": g["away_moneyline"],
            "week": g["week"],
        })
        # Pre-game context the stat lines can't see: weather, roof, rest, rivalry.
        ctx = pd.DataFrame({
            "game_id": g["game_id"],
            "wind": pd.to_numeric(g["wind"], errors="coerce").fillna(0).where(~g["roof"].isin(["dome", "closed"]), 0),
            "temp": pd.to_numeric(g["temp"], errors="coerce").fillna(70).where(~g["roof"].isin(["dome", "closed"]), 70),
            "indoors": g["roof"].isin(["dome", "closed"]).astype(float),
            "div_game": pd.to_numeric(g["div_game"], errors="coerce").fillna(0),
            "home_rest": pd.to_numeric(g["home_rest"], errors="coerce").fillna(7),
            "away_rest": pd.to_numeric(g["away_rest"], errors="coerce").fillna(7),
            "playoff": (g["game_type"] != "REG").astype(float),
        })

        frames = []
        for season in seasons:
            age = 6 if season == current else None
            path = fetch(STATS_URL.format(season=season), cache / f"nfl_stats_team_week_{season}.csv", age)
            if path:
                frames.append(pd.read_csv(path, low_memory=False))
        st = pd.concat(frames, ignore_index=True)
        stat_cols = [c for c in st.columns if c not in DROP and pd.api.types.is_numeric_dtype(st[c])]
        st = st[["game_id", "team", *stat_cols]].copy()
        # Derived efficiency stats on top of the raw columns.
        plays = st["attempts"].fillna(0) + st["carries"].fillna(0) + st["sacks_suffered"].fillna(0)
        st["yards_per_play"] = (st["passing_yards"].fillna(0) + st["rushing_yards"].fillna(0)) / plays.replace(0, np.nan)
        st["turnovers"] = st["passing_interceptions"].fillna(0) + st["rushing_fumbles_lost"].fillna(0) \
            + st.get("sack_fumbles_lost", 0).fillna(0) + st.get("receiving_fumbles_lost", 0).fillna(0)
        att = st["attempts"].replace(0, np.nan)
        comp_c = ((st["completions"] / att - 0.3) * 5).clip(0, 2.375)
        yds_c = ((st["passing_yards"] / att - 3) * 0.25).clip(0, 2.375)
        td_c = (st["passing_tds"] / att * 20).clip(0, 2.375)
        int_c = (2.375 - st["passing_interceptions"] / att * 25).clip(0, 2.375)
        st["passer_rating"] = (comp_c + yds_c + td_c + int_c) / 6 * 100
        st["epa_per_play"] = (st["passing_epa"].fillna(0) + st["rushing_epa"].fillna(0)) / plays.replace(0, np.nan)
        st["team"] = st["team"].map(lambda t: NFL_TEAMS.get(t, t))

        long = []
        for side, other in (("home", "away"), ("away", "home")):
            part = games[["game_id", "date", "season", side, other, f"{side}_points", "neutral"]].rename(
                columns={side: "team", other: "opp", f"{side}_points": "points"})
            part["home"] = np.where(part["neutral"], 0.5, 1.0 if side == "home" else 0.0)
            long.append(part.drop(columns="neutral"))
        tg = pd.concat(long, ignore_index=True).merge(st, on=["game_id", "team"], how="left")
        tg = tg.merge(ctx, on="game_id", how="left")
        tg["my_rest"] = np.where(tg["home"] == 0, tg["away_rest"], tg["home_rest"])
        tg["their_rest"] = np.where(tg["home"] == 0, tg["home_rest"], tg["away_rest"])
        tg = tg.drop(columns=["home_rest", "away_rest"])
        return games, tg

    extra_cols = ["wind", "temp", "indoors", "div_game", "my_rest", "their_rest", "playoff"]

    def stat_columns(self, tg):
        return [c for c in super().stat_columns(tg) if c not in self.extra_cols]

    def season_of(self, date):
        return date.year if date.month >= 3 else date.year - 1


NFL = NFLStats(
    key="nfl", name="NFL", simulate=_sim,
    rating_params=RatingParams(multiplicative=False, home_adv=1.8, shrink=4, half_life_days=180),
    halflife=6, chunk_days=7, first_season=2006,
    experts=ExpertConfig(rf_min_leaf=30),
)
