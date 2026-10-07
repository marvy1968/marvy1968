"""Load history and upcoming slates for a sport from its data source."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .data import nflverse, oddsapi
from .data.cfbd import CFBDClient, pace_factors, to_games
from .data.espn import ESPNClient
from .models import Game
from .sports import Sport

log = logging.getLogger(__name__)


def load_games(sport: Sport, s: Settings, start: datetime, end: datetime,
               lines_from: datetime | None = None) -> tuple[list[Game], dict]:
    """All games (finished and scheduled) between start and end, plus sport context.

    College lines cost one request per week, so they are only fetched for weeks with games
    on or after lines_from (default: start).
    """
    lines_from = lines_from or start
    cache = Path(s.state_dir) / "cache"
    ctx: dict = {}
    if sport.source == "espn":
        client = ESPNClient(cache_dir=cache)
        games = []
        for path, league in sport.espn_paths:
            games += client.games(path, sport.key, start, end, league)
    elif sport.source == "nflverse":
        games = [g for g in nflverse.load_games(cache) if start <= g.start <= end]
    elif sport.source == "cfbd":
        client = CFBDClient(s.cfbd_api_key)
        games = []
        for year in range(start.year if start.month >= 7 else start.year - 1, end.year + 1):
            for season_type in ("regular", "postseason"):
                raw = client.games(year, season_type=season_type)
                weeks = sorted({(gm.get("week")) for gm in raw if gm.get("week") is not None})
                lines = []
                for week in weeks:
                    week_games = to_games([gm for gm in raw if gm.get("week") == week])
                    if any(lines_from <= g.start <= end for g in week_games):
                        lines += client.lines(year, week, season_type)
                games += [g for g in to_games(raw, lines) if start <= g.start <= end]
            try:
                ctx["pace"] = pace_factors(client.season_stats(year))
            except Exception as exc:  # pace is a refinement; never fail over it
                log.warning("CFB pace stats unavailable: %s", exc)
    elif sport.source == "euroleague":
        from .stats.euroleague import schedule_games
        games = schedule_games(start, end)
    else:
        raise ValueError(f"unknown source {sport.source}")
    return games, ctx


def load_for_run(sport: Sport, s: Settings, now: datetime, hours_ahead: int) -> tuple[list[Game], list[Game], dict]:
    """(history, upcoming slate, context) for a live run."""
    if sport.source == "cfbd":  # college ratings use the current season only
        season = now.year if now.month >= 7 else now.year - 1
        start = datetime(season, 7, 1, tzinfo=now.tzinfo)
    else:
        start = now - timedelta(days=sport.history_days)
    games, ctx = load_games(sport, s, start, now + timedelta(hours=hours_ahead), lines_from=now)
    history = [g for g in games if g.completed]
    slate = [g for g in games if not g.completed and now <= g.start <= now + timedelta(hours=hours_ahead)]
    if s.odds_api_key and sport.odds_api_keys and slate:
        events = []
        for key in sport.odds_api_keys:
            try:
                events += oddsapi.fetch(s.odds_api_key, key)
            except Exception as exc:
                log.warning("Odds API %s failed: %s", key, exc)
        matched = oddsapi.attach(slate, events)
        log.info("%s: Odds API matched %d/%d games", sport.key, matched, len(slate))
    return history, slate, ctx
