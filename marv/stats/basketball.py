"""NBA / WNBA stats module: ESPN team box scores (sportsdataverse) and, for the NBA, historical odds."""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.teams import normalize
from ..ratings import RatingParams
from ..sims import basketball
from .base import StatsModule, fetch
from .experts import ExpertConfig

BOX_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_{league}_team_boxscores/team_box_{season}.parquet"
NBA_ODDS_URL = "https://raw.githubusercontent.com/kyleskom/NBA-Machine-Learning-Sports-Betting/master/Data/OddsData.sqlite"
ID_COLS = {"game_id", "season", "season_type", "team_id", "opponent_team_id", "team_score", "opponent_team_score",
           "team_winner"}


class BasketballStats(StatsModule):
    league: str = "nba"

    def load(self, cache: Path, seasons: list[int], current: int | None = None):
        frames = []
        for season in seasons:
            age = 6 if season == current else None
            path = fetch(BOX_URL.format(league=self.league, season=season), cache / f"{self.league}_box_{season}.parquet", age)
            if path:
                frames.append(pd.read_parquet(path))
        box = pd.concat(frames, ignore_index=True)
        box = box[box["season_type"].isin([2, 3])].reset_index(drop=True)  # regular season + playoffs
        box = box.drop_duplicates(["game_id", "team_id"])
        box["date"] = pd.to_datetime(box["game_date"])
        stat_cols = [c for c in box.columns if c not in ID_COLS and pd.api.types.is_numeric_dtype(box[c])]
        # Derived four-factor style efficiency stats.
        fga, fta, tov = box["field_goals_attempted"], box["free_throws_attempted"], box["total_turnovers"]
        box["possessions"] = fga + 0.44 * fta - box["offensive_rebounds"] + tov
        box["efg"] = (box["field_goals_made"] + 0.5 * box["three_point_field_goals_made"]) / fga
        box["tov_rate"] = tov / box["possessions"]
        box["ft_rate"] = box["free_throws_made"] / fga
        box["off_rating"] = 100 * box["team_score"] / box["possessions"]
        stat_cols += ["possessions", "efg", "tov_rate", "ft_rate", "off_rating"]
        opp = box[["game_id", "team_id", "offensive_rebounds", "defensive_rebounds"]].rename(
            columns={"team_id": "opponent_team_id", "offensive_rebounds": "_opp_oreb", "defensive_rebounds": "_opp_dreb"})
        box = box.merge(opp, on=["game_id", "opponent_team_id"], how="left")
        box["oreb_pct"] = box["offensive_rebounds"] / (box["offensive_rebounds"] + box["_opp_dreb"])
        box["dreb_pct"] = box["defensive_rebounds"] / (box["defensive_rebounds"] + box["_opp_oreb"])
        stat_cols += ["oreb_pct", "dreb_pct"]

        tg = pd.DataFrame({
            "game_id": box["game_id"].astype(str), "date": box["date"].dt.normalize(), "season": box["season"].astype(int),
            "team": box["team_display_name"], "opp": box["opponent_team_display_name"],
            "home": np.where(box["team_home_away"] == "home", 1.0, 0.0), "points": box["team_score"].astype(float),
        })
        tg = pd.concat([tg, box[stat_cols].astype(float).reset_index(drop=True)], axis=1)
        home = tg[tg["home"] == 1]
        away = tg[tg["home"] == 0].set_index("game_id")
        games = pd.DataFrame({
            "game_id": home["game_id"].values, "date": home["date"].values, "season": home["season"].values,
            "home": home["team"].values, "away": away.loc[home["game_id"], "team"].values, "neutral": False,
            "home_points": home["points"].values, "away_points": away.loc[home["game_id"], "points"].values,
        })
        games = self.attach_odds(games, cache)
        return games, tg

    def attach_odds(self, games: pd.DataFrame, cache: Path) -> pd.DataFrame:
        for col in ("spread", "total", "home_ml", "away_ml"):
            games[col] = np.nan
        return games


class NBAStats(BasketballStats):
    league = "nba"

    def attach_odds(self, games, cache):
        games = super().attach_odds(games, cache)
        path = fetch(NBA_ODDS_URL, cache / "nba_odds.sqlite", max_age_hours=24)
        if not path:
            return games
        con = sqlite3.connect(path)
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        frames = []
        for season in sorted(int(x) for x in games["season"].dropna().unique()):
            label = f"{season - 1}-{str(season)[2:]}"
            for name in (label, f"odds_{label}_new", f"odds_{label}"):
                if name in tables:
                    df = pd.read_sql(f'select Date, Home, Away, OU, Spread, ML_Home, ML_Away from "{name}"', con)
                    if df["Date"].astype(str).str.match(r"\d{4}-\d{2}-\d{2}$").all():
                        frames.append(df)
                        break
        con.close()
        if not frames:
            return games
        odds = pd.concat(frames, ignore_index=True)
        odds["date"] = pd.to_datetime(odds["Date"])
        odds["key"] = odds["date"].dt.strftime("%Y-%m-%d") + "|" + odds["Home"].map(normalize)
        odds = odds.drop_duplicates("key").set_index("key")
        key = pd.to_datetime(games["date"]).dt.strftime("%Y-%m-%d") + "|" + games["home"].map(normalize)
        hit = key.map(lambda k: k in odds.index)
        sub = odds.reindex(key[hit])
        spread = pd.to_numeric(sub["Spread"].replace({"PK": 0, "pk": 0}), errors="coerce")
        games.loc[hit, "spread"] = -spread.values  # file: positive = home favored
        games.loc[hit, "total"] = pd.to_numeric(sub["OU"], errors="coerce").values
        games.loc[hit, "home_ml"] = pd.to_numeric(sub["ML_Home"], errors="coerce").values
        games.loc[hit, "away_ml"] = pd.to_numeric(sub["ML_Away"], errors="coerce").values
        return games

    def season_of(self, date):
        return date.year + 1 if date.month >= 9 else date.year


class WNBAStats(BasketballStats):
    league = "wnba"


def _nba_sim(h, a, n, rng):
    return basketball.simulate_game(h, a, basketball.NBA, n=n, rng=rng)


def _wnba_sim(h, a, n, rng):
    return basketball.simulate_game(h, a, basketball.WNBA, n=n, rng=rng)


NBA = NBAStats(key="nba", name="NBA", simulate=_nba_sim,
               rating_params=RatingParams(multiplicative=False, home_adv=2.5, shrink=5, half_life_days=60),
               halflife=12, chunk_days=14, first_season=2008,
               experts=ExpertConfig(rf_min_leaf=40, rf_trees=150))
WNBA = WNBAStats(key="wnba", name="WNBA", simulate=_wnba_sim,
                 rating_params=RatingParams(multiplicative=False, home_adv=2.5, shrink=5, half_life_days=60),
                 halflife=10, chunk_days=14, first_season=2010, has_lines=False,
                 experts=ExpertConfig(rf_min_leaf=25, rf_trees=150))
