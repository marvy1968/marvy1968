"""EuroLeague live scores for the in-game monitor (live.euroleague.net Header endpoint, no key).

ESPN doesn't carry EuroLeague, so each of today's games (from the EuroLeague schedule) is read from
the official live header: score, quarter and time left in the quarter. Field names vary a little
between seasons, so every read has fallbacks; `python -m marv live-probe --sport euroleague` prints
the raw response to check them.
"""

import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)
HEADER = "https://live.euroleague.net/api/Header"


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def header(season: int, gamecode: int, competition: str = "E") -> dict:
    resp = requests.get(HEADER, params={"gamecode": gamecode, "seasoncode": f"{competition}{season}"}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def apply_header(game, h: dict) -> None:
    """Fill the Game's live fields the monitor reads (state, period, clock, scores) from a header."""
    live = str(_get(h, "Live", "live", default="")).lower() in ("true", "1")
    home = _get(h, "ScoreA", "scoreA", "LocalScore")
    away = _get(h, "ScoreB", "scoreB", "RoadScore")
    quarter = _get(h, "Quarter", "quarter", "Period")
    clock = str(_get(h, "RemainingPartialTime", "remainingPartialTime", "Clock", default="") or "")
    try:
        period = int(str(quarter).strip().lstrip("Qq")) if quarter is not None else 0
    except ValueError:
        period = 0
    game.info["period"] = period
    game.info["clock"] = clock
    if home is not None and away is not None:
        game.info["live_home"], game.info["live_away"] = float(home), float(away)
    at_zero = clock.replace(":", "").strip("0") == "" and clock != ""
    if live:
        game.info["state"] = "in"
        game.info["status_name"] = "STATUS_END_PERIOD" if at_zero else "STATUS_IN_PROGRESS"
    elif home is not None and period >= 4 and at_zero:
        game.info["state"] = "post"
        game.info["status_name"] = "STATUS_FINAL"
        game.completed = True
        game.home_score, game.away_score = float(home), float(away)
    else:
        game.info["state"] = "pre"


def live_games(now: datetime) -> list:
    """Today's EuroLeague games (started in the last 4 hours) with live fields filled in."""
    from ..stats.euroleague import schedule_games
    out = []
    for g in schedule_games(now - timedelta(hours=18), now + timedelta(hours=6)):
        season, code = g.id[1:].split("-")
        try:
            apply_header(g, header(int(season), int(code)))
        except Exception as exc:
            log.warning("EuroLeague live header %s: %s", g.id, exc)
            continue
        if g.info.get("state") in ("in", "post"):
            out.append(g)
    return out
