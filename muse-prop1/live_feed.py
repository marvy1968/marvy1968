"""Muse Prop1 — live stats via ESPN site API (unofficial, no key).
Poll scoreboard + game summary every 30s (football) / 45s (basketball).
If data is >90s stale, caller must skip.
nflverse / CollegeFootballData / Torvik are for pregame priors only — NOT live.

ESPN summary boxscore shape (defensive parsing — ESPN is undocumented):
  boxscore.players: [{team: {...}, statistics: [
    {name: "passing", labels: ["C/ATT","YDS","TD","INT"],
     athletes: [{athlete: {id, displayName}, stats: ["12/18","142","1","0"]}]}]}]
  Basketball labels are like ["MIN","FG","3PT","FT","OREB","DREB","REB","AST","STL","BLK","TO","PF","PTS"].
  Clock: header.competitions[0].status: {period, displayClock: "12:34",
    type: {state: "in"}}. Football quarters are 15 min, NCAAB halves 20 min,
    NCAAW quarters 10 min.

If a player's stats cannot be parsed, that player is omitted — never guessed.
If a game has no parseable players, the game is returned with players={} and
the runner skips it (fail-closed, logs the reason).
"""
from __future__ import annotations
import re, time
import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports"

ESPN_PATHS = {
    "nfl": "football/nfl",
    "ncaaf": "football/college-football",
    "ncaab": "basketball/mens-college-basketball",
    "ncaaw": "basketball/womens-college-basketball",
    "nba": "basketball/nba",
}

PERIOD_MIN = {"nfl": 15, "ncaaf": 15, "ncaab": 20, "ncaaw": 10, "nba": 12}

def fetch_live_games(sport: str, timeout: int = 10) -> dict:
    if sport not in ESPN_PATHS:
        raise ValueError(f"unknown sport {sport}")
    r = requests.get(f"{BASE}/{ESPN_PATHS[sport]}/scoreboard", timeout=timeout)
    r.raise_for_status()
    return {"sport": sport, "as_of": time.time(), "payload": r.json()}

def fetch_game_summary(sport: str, event_id: str, timeout: int = 10) -> dict:
    if sport not in ESPN_PATHS:
        raise ValueError(f"unknown sport {sport}")
    r = requests.get(f"{BASE}/{ESPN_PATHS[sport]}/summary",
                     params={"event": event_id}, timeout=timeout)
    r.raise_for_status()
    return {"sport": sport, "as_of": time.time(), "payload": r.json()}

def _num(x) -> float:
    """Parse '142', '12/18' (take second part for C/ATT), '3-5', '--' -> 0."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s in ("", "--", "-"):
        return 0.0
    if "/" in s:
        parts = s.split("/")
        try:
            return float(parts[-1])
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0

def _clock_to_elapsed(sport: str, period: int, display_clock: str) -> tuple[float, float]:
    """Return (elapsed_min, remaining_min) in game-clock minutes.
    NBA clocks are "M:SS" normally but "SS.S" (seconds with tenths) under a
    minute — the colon regex misses those, so try a bare-seconds parse too.
    College clocks are always "M:SS", so this does not change college behaviour."""
    from engine import SPORTS
    total = SPORTS[sport]["total_min"]
    pm = PERIOD_MIN[sport]
    m = re.match(r"(\d+):(\d+)", display_clock or "")
    if m:
        remaining_in_period = int(m.group(1)) + int(m.group(2)) / 60.0
    else:
        try:
            remaining_in_period = float(display_clock) / 60.0
        except (TypeError, ValueError):
            return 0.0, total
    if period < 1:
        return 0.0, total
    elapsed = (period - 1) * pm + (pm - remaining_in_period)
    elapsed = min(max(elapsed, 0.0), total)
    return elapsed, total - elapsed

def parse_scoreboard(payload: dict, sport: str) -> list[dict]:
    """Return live games only: [{game_id, home, away, home_score, away_score,
    period, display_clock, elapsed_min, remaining_min, state}]."""
    inner = payload.get("payload", payload) if isinstance(payload, dict) else {}
    games = []
    for event in inner.get("events", []) or []:
        comp = (event.get("competitions") or [{}])[0]
        status = comp.get("status", {}) or {}
        state = (status.get("type") or {}).get("state", "")
        if state != "in":
            continue
        period = int(status.get("period", 0) or 0)
        clock = status.get("displayClock", "") or ""
        elapsed, remaining = _clock_to_elapsed(sport, period, clock)
        competitors = comp.get("competitors", []) or []
        home = away = None
        home_score = away_score = 0
        for c in competitors:
            team = (c.get("team") or {}).get("displayName", "")
            score = int(_num(c.get("score")))
            if c.get("homeAway") == "home":
                home, home_score = team, score
            else:
                away, away_score = team, score
        games.append({
            "game_id": str(event.get("id", "")), "sport": sport,
            "home": home, "away": away, "home_score": home_score, "away_score": away_score,
            "period": period, "display_clock": clock,
            "elapsed_min": elapsed, "remaining_min": remaining,
            "state": state, "as_of": payload.get("as_of"),
        })
    return games

# label -> (our stat key, group) — group disambiguates YDS/TD across passing/rushing/receiving
_FB_MAP = {
    ("passing", "YDS"): "pass_yds", ("passing", "TD"): "pass_tds",
    ("passing", "INT"): "pass_int",
    ("rushing", "YDS"): "rush_yds", ("rushing", "TD"): "rush_tds",
    ("rushing", "CAR"): "rush_att",
    ("receiving", "YDS"): "rec_yds", ("receiving", "TD"): "rec_tds",
    ("receiving", "REC"): "receptions",
}
_BB_MAP = {
    "MIN": "minutes", "PTS": "points", "REB": "rebounds", "AST": "assists",
    "STL": "steals", "BLK": "blocks", "TO": "turnovers", "PF": "fouls",
    "3PT": "threes", "OREB": "oreb", "DREB": "dreb",
}

def _made_attempt(dv) -> tuple[int, int]:
    """Parse an ESPN 'M-A' display value like '40-86' -> (40, 86)."""
    if dv is None:
        return 0, 0
    parts = str(dv).strip().split("-")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


def _parse_team_totals(boxscore: dict, home_id: str | None,
                       away_id: str | None) -> tuple[dict, dict | None]:
    """Parse the boxscore team block into per-team totals and possessions.
    Returns (team_totals, possessions) where team_totals is
    {team_id: {points, fga, fta, oreb, tov, rebounds, assists, threes}} and
    possessions is {"home": x, "away": y, "game": z} (game = mean of the two
    teams, Dean Oliver formula — see engine.team_possessions), or None if the
    team block is missing/unparseable. Rebounds/OREB are read straight from
    the boxscore team block, so they follow each league's own definition
    (college boxscores include team rebounds; NBA boxscores are player-only
    — see pace-props-nba-test-spec-2026-11-08)."""
    from engine import team_possessions
    totals: dict[str, dict] = {}
    for tstat in boxscore.get("teams", []) or []:
        team = tstat.get("team") or {}
        tid = str(team.get("id")) if team.get("id") else None
        if not tid:
            continue
        row: dict[str, float] = {"points": 0.0, "fga": 0.0, "fta": 0.0,
                                 "oreb": 0.0, "tov": 0.0, "rebounds": 0.0,
                                 "assists": 0.0, "threes": 0.0}
        for st in tstat.get("statistics", []) or []:
            name = str(st.get("name", ""))
            dv = st.get("displayValue")
            if name == "fieldGoalsMade-fieldGoalsAttempted":
                _, att = _made_attempt(dv)
                row["fga"] = float(att)
            elif name == "freeThrowsMade-freeThrowsAttempted":
                _, att = _made_attempt(dv)
                row["fta"] = float(att)
            elif name == "threePointFieldGoalsMade-threePointFieldGoalsAttempted":
                made, _ = _made_attempt(dv)
                row["threes"] = float(made)
            elif name == "totalRebounds":
                row["rebounds"] = _num(dv)
            elif name == "offensiveRebounds":
                row["oreb"] = _num(dv)
            elif name == "assists":
                row["assists"] = _num(dv)
            elif name == "turnovers":
                row["tov"] = _num(dv)
        totals[tid] = row
    if not totals:
        return {}, None
    possessions = None
    if home_id in totals and away_id in totals:
        ph = team_possessions(totals[home_id]["fga"], totals[home_id]["oreb"],
                              totals[home_id]["tov"], totals[home_id]["fta"])
        pa = team_possessions(totals[away_id]["fga"], totals[away_id]["oreb"],
                              totals[away_id]["tov"], totals[away_id]["fta"])
        possessions = {"home": ph, "away": pa, "game": (ph + pa) / 2.0}
    return totals, possessions


def parse_summary(payload: dict, sport: str) -> dict:
    """Parse one ESPN summary into a normalized game state.
    Returns {game_id, sport, elapsed_min, remaining_min, period, home_score,
    away_score, players: {name: {stats...}}, team_plays, parse_notes}.
    team_plays is best-effort (football only); None if unavailable.
    Basketball also returns home_team_id, away_team_id,
    team_totals {team_id: {points, fga, fta, oreb, tov, rebounds, assists,
    threes}} and possessions {"home", "away", "game"} (None if the team
    block is missing/unparseable).
    """
    inner = payload.get("payload", payload) if isinstance(payload, dict) else {}
    header = inner.get("header", {}) or {}
    comp = (header.get("competitions") or [{}])[0]
    status = comp.get("status", {}) or {}
    period = int(status.get("period", 0) or 0)
    clock = status.get("displayClock", "") or ""
    elapsed, remaining = _clock_to_elapsed(sport, period, clock)
    competitors = comp.get("competitors", []) or []
    home_score = away_score = 0
    home_team_id = away_team_id = None
    for c in competitors:
        tid = str((c.get("team") or {}).get("id")) if (c.get("team") or {}).get("id") else None
        if c.get("homeAway") == "home":
            home_score = int(_num(c.get("score")))
            home_team_id = tid
        else:
            away_score = int(_num(c.get("score")))
            away_team_id = tid
    game_id = str(header.get("id", "") or "")
    players: dict[str, dict] = {}
    notes: list[str] = []
    boxscore = inner.get("boxscore", {}) or {}
    team_plays = None
    # Team totals (football): look for a "team" statistics block with total plays
    for tstat in boxscore.get("teams", []) or []:
        for st in tstat.get("statistics", []) or []:
            if str(st.get("name", "")).lower() in ("total plays", "plays", "totalplays"):
                try:
                    team_plays = int(_num(st.get("displayValue")))
                except (TypeError, ValueError):
                    pass
    # Team totals + possessions (basketball)
    team_totals: dict = {}
    possessions = None
    if sport in ("ncaab", "ncaaw", "nba"):
        team_totals, possessions = _parse_team_totals(boxscore, home_team_id, away_team_id)
        if home_team_id in team_totals:
            team_totals[home_team_id]["points"] = float(home_score)
        if away_team_id in team_totals:
            team_totals[away_team_id]["points"] = float(away_score)
        if possessions is None:
            notes.append("no parseable team totals in ESPN summary")
    for entry in boxscore.get("players", []) or []:
        for group in entry.get("statistics", []) or []:
            gname = str(group.get("name", "")).lower()
            labels = group.get("labels") or group.get("keys") or []
            for ath in group.get("athletes", []) or []:
                a = ath.get("athlete") or {}
                name = a.get("displayName") or a.get("shortName") or ""
                if not name:
                    continue
                stats = ath.get("stats") or []
                row = players.setdefault(name, {"name": name, "player_id": a.get("id")})
                for label, val in zip(labels, stats):
                    lab = str(label).strip().upper()
                    if sport in ("nfl", "ncaaf"):
                        key = _FB_MAP.get((gname, lab))
                        if key:
                            row[key] = _num(val)
                    else:
                        key = _BB_MAP.get(lab)
                        if key:
                            # MIN can be "22:14" — convert to minutes
                            if key == "minutes" and isinstance(val, str) and ":" in val:
                                mm, ss = val.split(":", 1)
                                try:
                                    row[key] = int(mm) + int(ss) / 60.0
                                except ValueError:
                                    row[key] = 0.0
                            else:
                                row[key] = _num(val)
    # PRA combos for basketball
    if sport in ("ncaab", "ncaaw", "nba"):
        for row in players.values():
            row["pra"] = row.get("points", 0) + row.get("rebounds", 0) + row.get("assists", 0)
            row["pr"] = row.get("points", 0) + row.get("rebounds", 0)
            row["pa"] = row.get("points", 0) + row.get("assists", 0)
            row["ra"] = row.get("rebounds", 0) + row.get("assists", 0)
    if not players:
        notes.append("no parseable player stats in ESPN summary")
    return {
        "game_id": game_id, "sport": sport, "elapsed_min": elapsed,
        "remaining_min": remaining, "period": period,
        "home_score": home_score, "away_score": away_score,
        "home_team_id": home_team_id, "away_team_id": away_team_id,
        "team_totals": team_totals, "possessions": possessions,
        "players": players, "team_plays": team_plays,
        "parse_notes": notes, "as_of": payload.get("as_of"),
    }
