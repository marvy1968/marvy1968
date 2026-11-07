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
}

PERIOD_MIN = {"nfl": 15, "ncaaf": 15, "ncaab": 20, "ncaaw": 10}

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
    """Return (elapsed_min, remaining_min) in game-clock minutes."""
    from engine import SPORTS
    total = SPORTS[sport]["total_min"]
    pm = PERIOD_MIN[sport]
    m = re.match(r"(\d+):(\d+)", display_clock or "")
    if not m or period < 1:
        return 0.0, total
    remaining_in_period = int(m.group(1)) + int(m.group(2)) / 60.0
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

def parse_summary(payload: dict, sport: str) -> dict:
    """Parse one ESPN summary into a normalized game state.
    Returns {game_id, sport, elapsed_min, remaining_min, period, home_score,
    away_score, players: {name: {stats...}}, team_plays, parse_notes}.
    team_plays is best-effort (football only); None if unavailable.
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
    for c in competitors:
        if c.get("homeAway") == "home":
            home_score = int(_num(c.get("score")))
        else:
            away_score = int(_num(c.get("score")))
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
    if sport in ("ncaab", "ncaaw"):
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
        "players": players, "team_plays": team_plays,
        "parse_notes": notes, "as_of": payload.get("as_of"),
    }
