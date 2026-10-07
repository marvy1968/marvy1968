"""Plugin layer + SQLite audit log, matching the Marv Predict Bot Max blueprint interface.

    from marv.plugins import MarvSportPlugin, AuditDatabase
    plugins = [MarvSportPlugin("mlb"), MarvSportPlugin("nba"), MarvSportPlugin("nfl")]

Each plugin implements the blueprint's BaseSportPlugin contract (fetch_slate_data,
run_simulation, apply_sport_vetos) on top of Marv's real data, stats experts, Monte Carlo and
veto stack, so a blueprint-style main() loop runs on real projections instead of placeholders.
"""

import datetime
import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)


class BaseSportPlugin(ABC):
    def __init__(self, sport_name: str):
        self.sport_name = sport_name

    @abstractmethod
    def fetch_slate_data(self, date_str: str) -> List[Dict[str, Any]]:
        """Fetch schedule, odds, and inputs from sport-specific APIs."""

    @abstractmethod
    def run_simulation(self, game_payload: Dict[str, Any]) -> Tuple[float, float]:
        """Returns (projected total, confidence of the projected winner)."""

    @abstractmethod
    def apply_sport_vetos(self, game_payload: Dict[str, Any]) -> bool:
        """True when the game should be skipped."""


class MarvSportPlugin(BaseSportPlugin):
    """Blueprint plugin backed by Marv. One fetch runs the full Marv pipeline for the slate."""

    def __init__(self, sport_key: str, settings=None, hours_ahead: int = 26):
        from .config import Settings
        from .sports import SPORTS
        self.sport = SPORTS[sport_key]
        self.settings = settings or Settings.from_env()
        self.hours_ahead = hours_ahead
        super().__init__(self.sport.name)

    def fetch_slate_data(self, date_str: str = "today") -> List[Dict[str, Any]]:
        from .config import Settings
        from .engine import predict
        from .sources import load_for_run
        from .stats import registry

        now = datetime.datetime.now(datetime.timezone.utc)
        history, slate, ctx = load_for_run(self.sport, self.settings, now, self.hours_ahead)
        projections = None
        if self.settings.stats_model and self.sport.key in registry.MODULES and slate:
            projections = registry.project(self.sport.key, slate, self.settings, now, history=history)
        preds = predict(self.sport, history, slate, now, self.settings.simulations, ctx,
                        veto=Settings.veto_for(self.sport.key),
                        model_weight=Settings.model_weight(self.sport.key), projections=projections)
        out = []
        for p in preds:
            g, o = p.game, p.game.odds
            out.append({"game_id": g.id, "home": g.home, "away": g.away, "start": g.start.isoformat(),
                        "total_line": o.total, "spread": o.spread, "home_ml": o.home_ml, "away_ml": o.away_ml,
                        "prediction": p})
        return out

    def run_simulation(self, game_payload: Dict[str, Any]) -> Tuple[float, float]:
        p = game_payload["prediction"]
        return p.model_total, max(p.home_win, 1 - p.home_win)

    def apply_sport_vetos(self, game_payload: Dict[str, Any]) -> bool:
        return not any(pk.active for pk in game_payload["prediction"].picks)


class AuditDatabase:
    """The blueprint's execution_logs table, extended with market/side/line/price and veto reasons."""

    def __init__(self, db_path: str | Path = "marv_bot_audit.db"):
        self.db_path = str(db_path)
        self.init_db()

    def init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, sport TEXT, game_matchup TEXT, projection REAL, confidence REAL,
                    veto_status TEXT, game_id TEXT, market TEXT, side TEXT, line REAL, price REAL,
                    edge REAL, reasons TEXT
                )""")

    def log_run(self, sport: str, matchup: str, projection: float, confidence: float, veto_status: str,
                game_id: str = "", market: str = "", side: str = "", line: float | None = None,
                price: float | None = None, edge: float | None = None, reasons: str = "") -> None:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""INSERT INTO execution_logs (timestamp, sport, game_matchup, projection, confidence,
                            veto_status, game_id, market, side, line, price, edge, reasons)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (ts, sport, matchup, projection, confidence, veto_status, game_id, market, side,
                          line, price, edge, reasons))

    def log_predictions(self, sport: str, preds) -> None:
        """Every pick Marv evaluated, locked or vetoed, with the reasons."""
        for p in preds:
            g = p.game
            matchup = f"{g.away} at {g.home}"
            for pk in p.picks:
                self.log_run(sport, matchup, p.model_total, pk.prob, "LOCKED_PLAY" if pk.active else "VETOED",
                             g.id, pk.market, pk.side, pk.line, pk.price, pk.edge, "; ".join(pk.vetoes))
