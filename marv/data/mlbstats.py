"""MLB Stats API (statsapi.mlb.com): current-season box scores and probable pitchers.

Retrosheet game logs only appear after a season ends, so in-season stats come from here,
mapped onto the same columns as marv.stats.mlb. Box scores are cached per game.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)
API = "https://statsapi.mlb.com/api/v1"

BAT_MAP = {"ab": "atBats", "h": "hits", "doubles": "doubles", "triples": "triples", "hr": "homeRuns",
           "rbi": "rbi", "sh": "sacBunts", "sf": "sacFlies", "hbp": "hitByPitch", "bb": "baseOnBalls",
           "ibb": "intentionalWalks", "so": "strikeOuts", "sb": "stolenBases", "cs": "caughtStealing",
           "gidp": "groundIntoDoublePlay", "ci": "catchersInterference", "lob": "leftOnBase"}
PIT_MAP = {"team_er": "earnedRuns", "wp": "wildPitches", "balks": "balks"}
FLD_MAP = {"po": "putOuts", "a": "assists", "e": "errors", "pb": "passedBall"}


def schedule(start: date, end: date, session: requests.Session | None = None) -> list[dict]:
    s = session or requests.Session()
    resp = s.get(f"{API}/schedule", timeout=60, params={
        "sportId": 1, "startDate": start.isoformat(), "endDate": end.isoformat(),
        "hydrate": "probablePitcher,team", "gameType": "R,F,D,L,W"})
    resp.raise_for_status()
    return [g for d in resp.json().get("dates", []) for g in d.get("games", [])]


def boxscore(game_pk: int, cache: Path, session: requests.Session | None = None) -> dict | None:
    path = cache / "mlbstats" / f"box_{game_pk}.json"
    if path.exists():
        return json.loads(path.read_text())
    s = session or requests.Session()
    try:
        resp = s.get(f"{API}/game/{game_pk}/boxscore", timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("MLB boxscore %s failed: %s", game_pk, exc)
        return None
    data = resp.json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def team_line(box: dict, side: str) -> dict:
    t = box["teams"][side]
    stats = t.get("teamStats", {})
    bat, pit, fld = stats.get("batting", {}), stats.get("pitching", {}), stats.get("fielding", {})
    row = {k: float(bat.get(v, 0) or 0) for k, v in BAT_MAP.items()}
    row.update({k: float(pit.get(v, 0) or 0) for k, v in PIT_MAP.items()})
    row.update({k: float(fld.get(v, 0) or 0) for k, v in FLD_MAP.items()})
    pitchers = t.get("pitchers", [])
    row["pitchers_used"] = float(len(pitchers))
    row["ind_er"] = row["team_er"]
    row["dp"] = float(fld.get("doublePlays", 0) or 0)
    row["tp"] = float(fld.get("triplePlays", 0) or 0)
    row["sp"] = f"mlbam{pitchers[0]}" if pitchers else None
    return row


def season_games(season: int, cache: Path, until: datetime) -> tuple[list[dict], list[dict]]:
    """(finished games with box lines, upcoming games with probables) for the current season."""
    session = requests.Session()
    start = date(season, 3, 1)
    games = schedule(start, min(until.date() + timedelta(days=2), date(season, 11, 30)), session)
    finished, upcoming = [], []
    for g in games:
        state = g.get("status", {}).get("abstractGameState")
        teams = g["teams"]
        rec = {"game_pk": g["gamePk"], "date": g.get("officialDate") or g["gameDate"][:10],
               "start": g["gameDate"], "home": teams["home"]["team"]["name"], "away": teams["away"]["team"]["name"],
               "home_runs": teams["home"].get("score"), "away_runs": teams["away"].get("score"),
               "home_sp": (teams["home"].get("probablePitcher") or {}).get("id"),
               "away_sp": (teams["away"].get("probablePitcher") or {}).get("id"),
               "game_type": g.get("gameType")}
        if state == "Final" and rec["home_runs"] is not None:
            box = boxscore(g["gamePk"], cache, session)
            if box:
                rec["home_line"], rec["away_line"] = team_line(box, "home"), team_line(box, "away")
                finished.append(rec)
        elif state == "Preview":
            upcoming.append(rec)
    return finished, upcoming
