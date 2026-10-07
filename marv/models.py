"""Shared data types used by every sport."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Odds:
    """Market snapshot. Spreads are from the home team's view (negative = home favored)."""
    provider: str = ""
    spread: float | None = None
    spread_open: float | None = None
    home_spread_price: float = -110
    away_spread_price: float = -110
    total: float | None = None
    total_open: float | None = None
    over_price: float = -110
    under_price: float = -110
    home_ml: float | None = None
    away_ml: float | None = None
    draw_ml: float | None = None  # soccer 1X2 only


@dataclass
class Game:
    id: str
    sport: str
    start: datetime
    home: str
    away: str
    neutral: bool = False
    completed: bool = False
    home_score: float | None = None
    away_score: float | None = None
    league: str = ""
    week: int | None = None
    odds: Odds | None = None
    info: dict = field(default_factory=dict)  # sport extras, e.g. probable pitchers


@dataclass
class Pick:
    market: str  # spread | total | ml | draw
    side: str  # team name, "Over", "Under" or "Draw"
    line: float | None  # handicap / total / American price for ml
    price: float  # American odds the pick is evaluated at
    prob: float  # model win probability (pushes excluded)
    ev: float  # expected profit per 1 unit staked
    edge: float  # EV expressed as probability points over break-even
    vetoes: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return not self.vetoes


@dataclass
class Prediction:
    game: Game
    home_exp: float
    away_exp: float
    model_margin: float
    model_total: float
    home_win: float
    draw: float = 0.0
    notes: list[str] = field(default_factory=list)
    picks: list[Pick] = field(default_factory=list)
