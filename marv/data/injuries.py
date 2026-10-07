"""Late-injury veto: flag games where a key player is newly Out / Doubtful / Questionable.

Key players come from free box-score data:
  * NFL: the quarterback who threw the most passes in his team's latest game (nflverse).
  * NBA / WNBA: each team's top-6 players by minutes over its last 15 games who also played in
    one of its last 3 games (sportsdataverse player box scores). Long-term absences are already
    reflected in the team's recent stats, so only fresh absences trigger the veto.
Injury statuses come from ESPN's public injuries feed.
"""

import logging
from pathlib import Path

import pandas as pd
import requests

from ..data.teams import NFL_TEAMS, normalize
from ..stats.base import fetch

log = logging.getLogger(__name__)
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/{path}/injuries"
NFL_PLAYERS = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
BOX_PLAYERS = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
               "espn_{league}_player_boxscores/player_box_{season}.parquet")
RISKY = {"out", "doubtful", "questionable", "day-to-day", "game time decision", "gtd"}
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "wnba": "basketball/wnba"}


def espn_injuries(sport: str) -> dict[str, list[tuple[str, str, str]]]:
    """{normalized team: [(player, status, position)]} from ESPN; empty on any failure."""
    try:
        resp = requests.get(ESPN_INJURIES.format(path=PATHS[sport]), timeout=30,
                            headers={"User-Agent": "Mozilla/5.0 (marv-predict-bot)"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("injury feed unavailable for %s: %s", sport, exc)
        return {}
    out: dict[str, list] = {}
    for team in data.get("injuries", []):
        name = team.get("displayName") or (team.get("team") or {}).get("displayName", "")
        rows = []
        for inj in team.get("injuries", []):
            athlete = inj.get("athlete") or {}
            pos = ((athlete.get("position") or {}).get("abbreviation") or "").upper()
            rows.append((athlete.get("displayName", ""), str(inj.get("status", "")).lower(), pos))
        out[normalize(name)] = rows
    return out


def nfl_starting_qbs(cache: Path, season: int) -> dict[str, str]:
    path = fetch(NFL_PLAYERS.format(season=season), cache / f"nfl_players_week_{season}.csv", max_age_hours=12)
    if not path:
        return {}
    df = pd.read_csv(path, low_memory=False, usecols=["player_display_name", "position", "team", "week", "attempts"])
    qbs = df[(df["position"] == "QB") & (df["attempts"].fillna(0) > 0)]
    latest = qbs.sort_values(["team", "week", "attempts"]).groupby("team").tail(1)
    return {normalize(NFL_TEAMS.get(t, t)): n for t, n in zip(latest["team"], latest["player_display_name"])}


def basketball_key_players(cache: Path, league: str, season: int) -> dict[str, set[str]]:
    path = fetch(BOX_PLAYERS.format(league=league, season=season), cache / f"{league}_player_box_{season}.parquet",
                 max_age_hours=12)
    if not path:
        return {}
    df = pd.read_parquet(path, columns=["game_date", "athlete_display_name", "team_display_name", "minutes", "did_not_play"])
    df["minutes"] = pd.to_numeric(df["minutes"], errors="coerce").fillna(0)
    out = {}
    for team, d in df.groupby("team_display_name"):
        dates = sorted(d["game_date"].unique())
        recent15, recent3 = set(dates[-15:]), set(dates[-3:])
        mins = d[d["game_date"].isin(recent15)].groupby("athlete_display_name")["minutes"].mean().nlargest(6)
        active = set(d[d["game_date"].isin(recent3) & (d["minutes"] > 0)]["athlete_display_name"])
        out[normalize(team)] = {p for p in mins.index if p in active}
    return out


def injury_vetoes(sport: str, cache: Path, season: int, teams: list[str]) -> dict[str, list[str]]:
    """{team: [veto reasons]} for teams with a key player at risk tonight."""
    if sport not in PATHS:
        return {}
    feed = espn_injuries(sport)
    if not feed:
        return {}
    if sport == "nfl":
        key = {t: {qb} for t, qb in nfl_starting_qbs(cache, season).items()}
    else:
        key = basketball_key_players(cache, sport, season)
    out = {}
    for team in teams:
        t = normalize(team)
        players = {normalize(p) for p in key.get(t, set())}
        reasons = [f"{name} ({status})" for name, status, pos in feed.get(t, [])
                   if normalize(name) in players and status in RISKY]
        if reasons:
            out[team] = [f"key injury: {', '.join(reasons)}"]
    return out
