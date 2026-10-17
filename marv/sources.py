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


def _cfbd_games(s: Settings, start: datetime, end: datetime, lines_from: datetime, ctx: dict) -> list[Game]:
    client = CFBDClient(s.cfbd_api_key, cache_dir=Path(s.state_dir) / "cache" / "cfbd_api")
    games: list[Game] = []
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
    return games


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
    elif sport.source == "cfbd" and not s.cfbd_api_key:
        # No CFBD key: FBS schedule, scores and ESPN lines from ESPN (Odds API lines replace them when set).
        client = ESPNClient(cache_dir=cache)
        games = client.games("football/college-football?groups=80", sport.key, start, end)
    elif sport.source == "cfbd":
        try:
            games = _cfbd_games(s, start, end, lines_from, ctx)
        except Exception as exc:  # CFBD down or slow: the free ESPN FBS schedule and lines keep the run alive
            log.warning("CFBD unavailable (%s); using ESPN schedule and lines", exc)
            games = ESPNClient(cache_dir=cache).games("football/college-football?groups=80", sport.key, start, end)
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
        matched = oddsapi.attach(slate, events, book=s.odds_book)
        log.info("%s: Odds API matched %d/%d games", sport.key, matched, len(slate))
    if sport.key == "cfb" and slate:
        n = bovada_fill(slate, Path(s.state_dir))
        if n:
            log.info("cfb: %d games got cached Bovada lines (0 credits)", n)
    return history, slate, ctx


def bovada_fill(slate, state: Path) -> int:
    """2026-10-17: games with no odds get Bovada's cached line (state/bovada_prices.json, saved by the edge board)."""
    import json
    from .data.teams import similarity
    from .models import Odds
    try:
        evs = (json.loads((state / "bovada_prices.json").read_text()).get("cfb") or {}).get("events", [])
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for g in slate:
        if g.odds and (g.odds.total is not None or g.odds.spread is not None):
            continue
        best, sc = None, 0.0
        for e in evs:
            try:
                st = datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            if abs(st - g.start) > timedelta(hours=12):
                continue
            v = min(similarity(g.home, e["home"]), similarity(g.away, e["away"]))
            if v > sc:
                best, sc = e, v
        if best is None or sc < 0.75:
            continue
        sp, tot, ml = best.get("spread") or [None, -110, -110], best.get("total") or [None, -110, -110], best.get("ml") or {}
        g.odds = Odds(provider="Bovada (cached)", spread=sp[0], home_spread_price=sp[1] or -110, away_spread_price=sp[2] or -110,
                      total=tot[0], over_price=tot[1] or -110, under_price=tot[2] or -110, home_ml=ml.get("home"), away_ml=ml.get("away"))
        n += 1
    return n
