"""NFL schedule, results and betting lines from the free nflverse games file."""

import csv
import io
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from ..models import Game, Odds
from .teams import NFL_TEAMS

URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ET = ZoneInfo("America/New_York")


def _f(value: str) -> float | None:
    try:
        return float(value) if value not in ("", "NA", None) else None
    except ValueError:
        return None


def parse_rows(rows) -> list[Game]:
    games = []
    for r in rows:
        try:
            start = datetime.strptime(f"{r['gameday']} {r.get('gametime') or '13:00'}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        home_score, away_score = _f(r.get("home_score")), _f(r.get("away_score"))
        game = Game(
            id=r["game_id"], sport="nfl", start=start.replace(tzinfo=ET).astimezone(timezone.utc),
            home=NFL_TEAMS.get(r["home_team"], r["home_team"]), away=NFL_TEAMS.get(r["away_team"], r["away_team"]),
            neutral=r.get("location") == "Neutral", completed=home_score is not None and away_score is not None,
            home_score=home_score, away_score=away_score, league=r.get("game_type", ""),
            week=int(r["week"]) if r.get("week") else None,
            info={"season": int(r["season"]), "espn_id": r.get("espn", "")},
        )
        spread_line = _f(r.get("spread_line"))  # positive = home favored
        if spread_line is not None or _f(r.get("total_line")) is not None:
            game.odds = Odds(
                provider="nflverse consensus",
                spread=-spread_line if spread_line is not None else None,
                home_spread_price=_f(r.get("home_spread_odds")) or -110,
                away_spread_price=_f(r.get("away_spread_odds")) or -110,
                total=_f(r.get("total_line")),
                over_price=_f(r.get("over_odds")) or -110,
                under_price=_f(r.get("under_odds")) or -110,
                home_ml=_f(r.get("home_moneyline")),
                away_ml=_f(r.get("away_moneyline")),
            )
        games.append(game)
    return games


def load_games(cache_dir: Path | None = None, max_age_hours: float = 3) -> list[Game]:
    cache = cache_dir / "nflverse_games.csv" if cache_dir else None
    if cache and cache.exists() and time.time() - cache.stat().st_mtime < max_age_hours * 3600:
        text = cache.read_text()
    else:
        resp = requests.get(URL, timeout=60)
        resp.raise_for_status()
        text = resp.text
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text)
    return parse_rows(csv.DictReader(io.StringIO(text)))
