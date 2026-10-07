"""Shared plumbing for the per-sport stats modules."""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

from ..models import Game
from ..ratings import RatingParams
from ..sims.base import SimResult
from .experts import ExpertConfig

log = logging.getLogger(__name__)

# Every module returns two frames:
GAME_COLS = ["game_id", "date", "season", "home", "away", "neutral", "home_points", "away_points",
             "spread", "total", "home_ml", "away_ml"]  # spread is the home line (negative = home favored)
TEAM_GAME_COLS = ["game_id", "date", "season", "team", "opp", "home", "points"]  # + stat columns


def fetch(url: str, path: Path, max_age_hours: float | None = None) -> Path | None:
    """Download url to path (cached). max_age_hours=None means cache forever (finished seasons)."""
    if path.exists() and (max_age_hours is None or time.time() - path.stat().st_mtime < max_age_hours * 3600):
        return path
    try:
        resp = requests.get(url, timeout=120)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("download failed %s: %s", url, exc)
        return path if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return path


@dataclass
class StatsModule:
    key: str
    name: str
    simulate: Callable[[float, float, int, np.random.Generator], SimResult]
    rating_params: RatingParams
    halflife: float  # EWMA half-life in games
    chunk_days: int  # walk-forward retrain cadence
    first_season: int
    experts: ExpertConfig = field(default_factory=ExpertConfig)
    has_lines: bool = True

    def load(self, cache: Path, seasons: list[int], current: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
        raise NotImplementedError

    def stat_columns(self, tg: pd.DataFrame) -> list[str]:
        """Every numeric box-score column (the "use every stat" default)."""
        skip = set(TEAM_GAME_COLS) | {"neutral"}
        return [c for c in tg.columns if c not in skip and pd.api.types.is_numeric_dtype(tg[c])]

    def focus(self, rows: pd.DataFrame, panel) -> pd.DataFrame:
        """Restrict which games get predictions (college football keeps only top-30 matchups)."""
        return rows

    def season_of(self, date: pd.Timestamp) -> int:
        return date.year


def games_to_objects(games: pd.DataFrame, sport: str) -> list[Game]:
    """Finished games as marv.models.Game for the power-rating expert."""
    out = []
    for r in games.itertuples(index=False):
        if pd.isna(r.home_points) or pd.isna(r.away_points):
            continue
        out.append(Game(id=str(r.game_id), sport=sport, start=pd.Timestamp(r.date).to_pydatetime(),
                        home=r.home, away=r.away, neutral=bool(r.neutral), completed=True,
                        home_score=float(r.home_points), away_score=float(r.away_points)))
    return out
