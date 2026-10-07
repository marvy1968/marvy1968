"""EuroLeague (men) stats module: official EuroLeague API via the `euroleague-api` package.

Team totals from each game's box score are mapped onto the ESPN basketball column layout, so
EuroLeague gets the same four factors, ratings and experts as the other basketball leagues.
Finished games are cached per game; the current season refreshes on every run.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..ratings import RatingParams
from ..sims import basketball
from .basketball import BasketballStats, _sim_for
from .experts import ExpertConfig

log = logging.getLogger(__name__)
COMPETITION = "E"

# EuroLeague box-score column -> ESPN-style column
COLUMN_MAP = {
    "Points": "team_score", "OffensiveRebounds": "offensive_rebounds", "DefensiveRebounds": "defensive_rebounds",
    "TotalRebounds": "total_rebounds", "Assistances": "assists", "Steals": "steals", "Turnovers": "total_turnovers",
    "BlocksFavour": "blocks", "BlocksAgainst": "blocks_against", "FoulsCommited": "fouls",
    "FoulsReceived": "fouls_drawn", "FreeThrowsMade": "free_throws_made", "FreeThrowsAttempted": "free_throws_attempted",
    "FieldGoalsMade3": "three_point_field_goals_made", "FieldGoalsAttempted3": "three_point_field_goals_attempted",
    "Valuation": "valuation",
}


def schedule_games(start, end) -> list:
    """Finished and upcoming EuroLeague games as marv Game objects (for the live run)."""
    from datetime import timezone
    from ..models import Game
    out = []
    for season in sorted({d.year - (0 if d.month >= 8 else 1) for d in (start, end)}):
        try:
            meta = season_meta(season)
        except Exception as exc:
            log.warning("EuroLeague schedule %s unavailable: %s", season, exc)
            continue
        for r in meta.itertuples():
            if pd.isna(r.date_parsed):
                continue
            when = r.date_parsed.to_pydatetime().replace(hour=18, tzinfo=timezone.utc)  # evening CET tip-offs
            if not (start <= when <= end):
                continue
            played = bool(r.played)
            out.append(Game(id=f"{COMPETITION}{season}-{int(r.gameCode)}", sport="euroleague", start=when,
                            home=str(r.hometeam), away=str(r.awayteam), completed=played,
                            home_score=float(r.homescore) if played else None,
                            away_score=float(r.awayscore) if played else None, league="EuroLeague"))
    return out


def season_meta(season: int) -> pd.DataFrame:
    from euroleague_api.EuroLeagueData import EuroLeagueData
    df = EuroLeagueData(COMPETITION).get_gamecodes_season(season)
    df["date_parsed"] = pd.to_datetime(df.get("date"), errors="coerce", format="mixed")
    return df


def game_totals(season: int, gamecode: int, cache: Path) -> pd.DataFrame | None:
    """Two rows (home, away) of team totals in ESPN layout; cached once the game is final."""
    path = cache / "euroleague" / f"{COMPETITION}{season}_{gamecode}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    try:
        from euroleague_api.boxscore_data import BoxScoreData
        bx = BoxScoreData(COMPETITION).get_players_boxscore_stats(season, gamecode)
    except Exception as exc:
        log.warning("EuroLeague box score %s/%s unavailable: %s", season, gamecode, exc)
        return None
    tot = bx[bx["Player"] == "Total"].copy()
    if len(tot) != 2:
        return None
    out = pd.DataFrame({new: pd.to_numeric(tot[old], errors="coerce").values
                        for old, new in COLUMN_MAP.items() if old in tot})
    fgm2 = pd.to_numeric(tot.get("FieldGoalsMade2"), errors="coerce").values
    fga2 = pd.to_numeric(tot.get("FieldGoalsAttempted2"), errors="coerce").values
    out["field_goals_made"] = fgm2 + out["three_point_field_goals_made"]
    out["field_goals_attempted"] = fga2 + out["three_point_field_goals_attempted"]
    out["team_home_away"] = np.where(tot["Home"].values == 1, "home", "away")
    out["team_display_name"] = tot["Team"].values
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path)
    return out


class EuroLeagueStats(BasketballStats):
    league = "euroleague"
    backfill_league = False  # the official API is the live source already

    def read_box(self, cache: Path, seasons: list[int], current: int | None) -> list[pd.DataFrame]:
        frames = []
        for season in seasons:
            try:
                meta = season_meta(season)
            except Exception as exc:
                log.warning("EuroLeague season %s schedule unavailable: %s", season, exc)
                continue
            rows = []
            for r in meta[meta["played"]].itertuples():
                tot = game_totals(season, int(r.gameCode), cache)
                if tot is None:
                    continue
                tot = tot.copy()
                gid = f"{COMPETITION}{season}-{int(r.gameCode)}"
                home_name, away_name = str(r.hometeam), str(r.awayteam)
                tot["team_display_name"] = np.where(tot["team_home_away"] == "home", home_name, away_name)
                tot["opponent_team_display_name"] = np.where(tot["team_home_away"] == "home", away_name, home_name)
                tot["team_id"] = tot["team_display_name"]
                tot["opponent_team_id"] = tot["opponent_team_display_name"]
                tot["game_id"] = gid
                tot["season"] = season
                tot["season_type"] = 3 if str(getattr(r, "Phase", "")).upper() in ("PO", "FF", "PI") else 2
                tot["game_date"] = r.date_parsed
                rows.append(tot)
            if rows:
                frames.append(pd.concat(rows, ignore_index=True))
        return frames

    def season_of(self, date):
        return date.year if date.month >= 8 else date.year - 1


EUROLEAGUE = EuroLeagueStats(key="euroleague", name="EuroLeague", simulate=_sim_for(basketball.EUROLEAGUE),
                             rating_params=RatingParams(multiplicative=False, home_adv=3.0, shrink=5, half_life_days=90),
                             halflife=8, chunk_days=14, first_season=2016, has_lines=False, adjust_schedule=True,
                             experts=ExpertConfig(rf_min_leaf=20, rf_trees=120, min_train_rows=300))
