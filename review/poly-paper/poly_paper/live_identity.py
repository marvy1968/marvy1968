"""Identity evidence for the live prop callers, from payloads they already fetch.

pm_nba_live.boxes and live_props.games download an ESPN scoreboard event, then
keep abbreviations and a short name. The scoreboard object also carries
competitor displayName and a scheduled `date`. Polymarket events fetched by
those callers carry team name fields and startTime. This module reads those
fields. It does not treat a market slug, a slug date, or an abbreviation as
the participants or the start.

If display names or an aware scheduled start are absent, nothing is built and
the fill stays rejected.
"""
from __future__ import annotations

import re

from .provider import _aware_instant

# Same question patterns as pm_nba_live.Q and live_props.Q.
_QUESTION = re.compile(r"Will (.+?) (?:record|throw) (?:at least )?(\d+)\+?", re.I)
_STAT = {
    "basketball_player_points": "PTS",
    "basketball_player_threes": "3PM",
    "basketball_player_rebounds": "REB",
    "basketball_player_assists": "AST",
    "basketball_player_steals": "STL",
    "basketball_player_blocks": "BLK",
    "basketball_player_points_rebounds_assists": "PRA",
    "football_player_passing_yards": "PYD",
    "football_player_rushing_yards": "RYD",
    "football_player_receiving_yards": "YDS",
    "football_player_receptions": "REC",
    "hockey_player_goals": "G",
    "hockey_player_assists": "A",
    "hockey_player_points": "PTS",
    "soccer_player_goals": "G",
}

_SPORT = {
    "NBA_LIVE": "nba",
    "NFL_LIVE": "nfl",
    "NHL_LIVE": "nhl",
    "EPL_LIVE": "epl",
}


class LiveEvidence:
    def __init__(self):
        self.espn = {}
        self.pm_events = []

    def record_espn_payload(self, payload):
        if not isinstance(payload, dict):
            return
        events = payload.get("events")
        if isinstance(events, list):
            for event in events:
                self._record_espn_event(event)
            return
        self._record_espn_event(payload)

    def _record_espn_event(self, event):
        if not isinstance(event, dict) or event.get("id") in (None, ""):
            return
        names, labeled = _espn_sides(event)
        start, status = _espn_start(event)
        if not names and start is None:
            return
        current = self.espn.get(str(event["id"]), {"players": []})
        self.espn[str(event["id"])] = {
            "id": str(event["id"]),
            "participants": names or current.get("participants") or [],
            "labeled": labeled or current.get("labeled") or {},
            "start": start if status == "ok" else current.get("start"),
            "start_status": status if status == "ok" else current.get("start_status", status),
            "players": current.get("players") or [],
        }

    def _merge_players(self, espn_id, payload):
        players = _box_players(payload if isinstance(payload, dict) else {})
        current = self.espn.setdefault(str(espn_id), {
            "id": str(espn_id), "participants": [], "start": None, "start_status": "missing", "players": [],
        })
        merged = {player["name"].casefold(): player for player in current.get("players") or []}
        for player in players:
            merged[player["name"].casefold()] = player
        current["players"] = list(merged.values())

    def record_pm_event(self, event):
        if not isinstance(event, dict) or not (event.get("markets") or event.get("teams")):
            return
        key = (event.get("id"), event.get("slug"))
        for index, old in enumerate(self.pm_events):
            if (old.get("id"), old.get("slug")) == key:
                if len(event.get("markets") or []) >= len(old.get("markets") or []):
                    self.pm_events[index] = event
                return
        self.pm_events.append(event)

    def note_http(self, url, payload):
        """Record an ESPN body the caller already downloaded. No network here."""
        if not isinstance(payload, dict):
            return
        if isinstance(payload.get("events"), list):
            self.record_espn_payload(payload)
            return
        text = str(url or "")
        espn_id = None
        if "event=" in text:
            espn_id = text.split("event=", 1)[1].split("&", 1)[0]
        if espn_id:
            self._merge_players(espn_id, payload)

    def build(self, *, key, pick, side, slug, lane_switch):
        espn_id = _espn_id(key)
        sport = _SPORT.get(str(lane_switch or "").upper())
        if espn_id is None or sport is None:
            return None
        espn = self.espn.get(espn_id)
        if not espn or len(espn["participants"]) != 2 or espn["start_status"] != "ok":
            return None
        explicit = [event for event in self.pm_events if _explicit_league(event) == _LEAGUE.get(sport)]
        if not explicit:
            return None
        player, line = _player_line(pick)
        stat = _stat_from_key(key)
        prop = None
        if player and stat and line is not None:
            roster = {item["name"].casefold(): item for item in espn["players"] if isinstance(item, dict)}
            athlete = roster.get(player.casefold())
            team_name = None if athlete is None else athlete.get("team")
            if athlete is None or not team_name or team_name not in set(espn["participants"]):
                return None
            prop = {"player": player, "stat": stat, "line": line, "side": "over" if side == "long" else "under"}
        home, away = _ordered_participants(espn)
        entry = {
            "sport": sport,
            "market": "prop" if prop else "ml",
            "home": home,
            "away": away,
            "start": espn["start"],
            "pick": pick,
            "key": key,
            "live": True,
            "source_game_id": espn_id,
            "prop": prop,
        }
        snapshot = {"events": [_enrich_prop_market(event, prop) for event in explicit]}
        return {"entry": entry, "snapshot": snapshot, "slug": slug}


_LEAGUE = {"nba": "nba", "nfl": "nfl", "nhl": "nhl", "epl": "epl", "ncaab": "cbb"}


def _espn_start(event):
    comp = (event.get("competitions") or [{}])[0]
    for value in (event.get("date"), comp.get("date") if isinstance(comp, dict) else None,
                  comp.get("startDate") if isinstance(comp, dict) else None):
        if not isinstance(value, str):
            continue
        instant, status = _aware_instant(value)
        if status == "ok":
            return instant.isoformat().replace("+00:00", "Z"), "ok"
        if status == "naive":
            return None, "naive"
    return None, "missing"


def _team_label(team):
    """Full club name. An abbreviation is not a participant."""
    if not isinstance(team, dict):
        return None
    abbrev = str(team.get("abbreviation") or "")
    for field in ("displayName", "name"):
        value = team.get(field)
        if isinstance(value, str) and value.strip() and value.strip() != abbrev:
            return value.strip()
    return None


def _ordered_participants(espn):
    labeled = espn.get("labeled") or {}
    names = espn["participants"]
    if labeled.get("home") in names and labeled.get("away") in names:
        return labeled["home"], labeled["away"]
    return names[0], names[1]


def _espn_sides(event):
    comps = (event.get("competitions") or [{}])[0].get("competitors") or []
    names = []
    labeled = {}
    for comp in comps:
        if not isinstance(comp, dict):
            continue
        label = _team_label(comp.get("team"))
        if not label:
            continue
        if label not in names:
            names.append(label)
        side = comp.get("homeAway")
        if side in ("home", "away"):
            labeled[side] = label
    return names, labeled


def _box_players(payload):
    found = []
    for team in (payload.get("boxscore") or {}).get("players") or []:
        label = _team_label(team.get("team"))
        for block in team.get("statistics") or []:
            for athlete in block.get("athletes") or []:
                name = ((athlete.get("athlete") or {}).get("displayName") if isinstance(athlete, dict) else None)
                if isinstance(name, str) and name.strip():
                    found.append({"name": name.strip(), "team": label})
    return found


def _explicit_league(event):
    for field in ("league", "sport"):
        value = event.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _espn_id(key):
    parts = str(key or "").split(":")
    if len(parts) >= 2 and parts[0].endswith("live") and parts[1]:
        return parts[1]
    return None


def _player_line(pick):
    match = _QUESTION.search(str(pick or ""))
    if not match:
        # The caller stores the parsed name in the pick: "Name 8+ PTS" or "Name NOT 8+ PTS".
        loose = re.match(r"(.+?)(?:\s+NOT)?\s+(\d+)\+", str(pick or ""))
        if not loose:
            return None, None
        return loose.group(1).strip(), loose.group(2)
    return match.group(1).strip(), match.group(2)


def _stat_from_key(key):
    parts = str(key or "").split(":")
    if len(parts) >= 4:
        return parts[3]
    return None


def _enrich_prop_market(event, prop):
    if not prop:
        return event
    copy = dict(event)
    markets = []
    for market in event.get("markets") or []:
        item = dict(market)
        if not item.get("player"):
            parsed = _QUESTION.search(str(item.get("question") or ""))
            if parsed and parsed.group(1).strip().casefold() == prop["player"].casefold():
                item["player"] = parsed.group(1).strip()
                item["line"] = parsed.group(2)
                item["stat"] = _STAT.get(item.get("sportsMarketType") or "", prop["stat"])
        markets.append(item)
    copy["markets"] = markets
    return copy
