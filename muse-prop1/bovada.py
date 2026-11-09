"""Muse Prop1 — Bovada odds via PropLine (third-party aggregator, NOT Bovada official).
Bovada has no public API. Do NOT scrape bovada.lv in v1.

PropLine response shape (verified against docs 2026-11-06, the-odds-api compatible):
  bulk: {"events": [{"id", "sport_key", "home_team", "away_team", "commence_time",
                     "bookmakers": [{"key": "bovada", "markets": [
                       {"key": "player_pass_yds", "last_update": "...",
                        "suspended_at": null, "line_type": "main",
                        "outcomes": [{"name": "Over", "description": "Josh Allen",
                                      "price": -110, "point": 245.5, "player_id": "espn:123"}]}]}]}]}
  single-event: same, without the outer "events" wrapper.
  Anytime TD: outcomes are Yes/No, point is usually null -> treat as 0.5.

Free tier: 1,000 req/day. Always bulk per sport, never per-game polling.
For 4 sports, polling all day at 30s exceeds free tier — poll only sports with
live games, stagger basketball, or use Hobby $9/mo (5,000/day).
Requires PROPLINE_API_KEY in env. No key = raises, never silently falls back to stale lines.
"""
from __future__ import annotations
import os, time
import requests

BASE = "https://api.prop-line.com/v1"

SPORT_KEYS = {
    "nfl": "football_nfl",
    "ncaaf": "football_ncaaf",
    "ncaab": "basketball_ncaab",
    # NCAAW key NOT confirmed in docs — verify via GET /v1/sports before first use.
    "ncaaw": "basketball_ncaaw",
}

MARKETS = {
    "nfl": ["player_pass_yds", "player_pass_tds", "player_rush_yds",
            "player_reception_yds", "player_receptions", "player_anytime_td"],
    "ncaaf": ["player_pass_yds", "player_pass_tds", "player_rush_yds",
              "player_reception_yds", "player_receptions", "player_anytime_td"],
    "ncaab": ["player_points", "player_rebounds", "player_assists", "player_threes",
              "player_points_rebounds", "player_points_assists",
              "player_rebounds_assists", "player_points_rebounds_assists"],
    "ncaaw": ["player_points", "player_rebounds", "player_assists", "player_threes",
              "player_points_rebounds", "player_points_assists",
              "player_rebounds_assists", "player_points_rebounds_assists"],
}

# Team-total market keys — NOT wired to fetch/evaluate yet. The projection
# (engine.project_team_total) and gate (engine.evaluate_team_pick) exist and
# are tested, but extract_bovada_lines is player-centric (it requires a
# `description` player name on outcomes) and the PropLine team-lines payload
# shape is unverified — no key on file. Do NOT add these to MARKETS until a
# real PropLine team-lines response has been inspected; guessing the shape
# would silently drop or misread lines.
TEAM_MARKETS = {
    "ncaab": ["team_points", "team_rebounds", "team_assists", "team_threes"],
    "ncaaw": ["team_points", "team_rebounds", "team_assists", "team_threes"],
}

def _api_key(api_key: str | None = None) -> str:
    key = api_key or os.environ.get("PROPLINE_API_KEY")
    if not key:
        raise RuntimeError("PROPLINE_API_KEY not set — set it in ~/workspace/muse-prop1/.env, do not paste it in chat")
    return key

def list_sports(api_key: str | None = None) -> dict:
    """Call once to verify sport keys — especially basketball_ncaaw."""
    r = requests.get(f"{BASE}/sports", params={"apiKey": _api_key(api_key)}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_bovada_props(sport: str, api_key: str | None = None) -> dict:
    """Return raw bulk odds payload + fetch timestamp for one sport."""
    if sport not in SPORT_KEYS:
        raise ValueError(f"unknown sport {sport}, expected one of {list(SPORT_KEYS)}")
    markets = ",".join(MARKETS[sport])
    url = f"{BASE}/sports/{SPORT_KEYS[sport]}/odds"
    r = requests.get(url, params={"apiKey": _api_key(api_key), "markets": markets,
                                  "bookmakers": "bovada"}, timeout=15)
    r.raise_for_status()
    return {"sport": sport, "as_of": time.time(), "payload": r.json()}

def _events_from(payload: dict) -> list[dict]:
    """Accept bulk {"events": [...]}, wrapped {"payload": {...}}, or a single event."""
    if not isinstance(payload, dict):
        return []
    inner = payload.get("payload", payload)
    if isinstance(inner, dict) and "events" in inner:
        return inner.get("events") or []
    if isinstance(inner, dict) and "bookmakers" in inner:
        return [inner]
    if isinstance(inner, list):
        return inner
    return []

def extract_bovada_lines(payload: dict, sport: str = "nfl") -> list[dict]:
    """Flatten to exact (game_id, player, market, line, over_price, under_price).
    Exact matching only — a 245.5 line is never treated as 249.5.
    Only line_type == 'main' rows are used; alternate lines are ignored in v1.
    Suspended markets (suspended_at set) are skipped.
    Returns [] on an unexpected shape — never guesses a line.
    """
    as_of = payload.get("as_of") if isinstance(payload, dict) else None
    grouped: dict[tuple, dict] = {}
    for event in _events_from(payload):
        game_id = str(event.get("id", ""))
        for book in event.get("bookmakers", []) or []:
            if book.get("key") != "bovada":
                continue
            for market in book.get("markets", []) or []:
                if market.get("suspended_at"):
                    continue
                if market.get("line_type") and market["line_type"] != "main":
                    continue
                mkey = market.get("key")
                if mkey not in MARKETS.get(sport, []):
                    continue
                for oc in market.get("outcomes", []) or []:
                    name = (oc.get("name") or "").strip()
                    player = (oc.get("description") or "").strip()
                    if not player or name not in ("Over", "Under", "Yes", "No"):
                        continue
                    point = oc.get("point")
                    if point is None:
                        point = 0.5
                    try:
                        point = float(point)
                        price = int(oc["price"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    side = "over" if name in ("Over", "Yes") else "under"
                    key = (game_id, mkey, player, point)
                    row = grouped.setdefault(key, {
                        "sport": sport, "game_id": game_id,
                        "home_team": event.get("home_team"), "away_team": event.get("away_team"),
                        "commence_time": event.get("commence_time"),
                        "player": player, "player_id": oc.get("player_id"),
                        "market": mkey, "line": point,
                        "over_price": None, "under_price": None,
                        "last_update": market.get("last_update"), "as_of": as_of,
                    })
                    if side == "over":
                        row["over_price"] = price
                    else:
                        row["under_price"] = price
                    if oc.get("player_id"):
                        row["player_id"] = oc["player_id"]
    out = [r for r in grouped.values()
           if r["over_price"] is not None and r["under_price"] is not None]
    return out

def find_line(lines: list[dict], game_id: str, player: str, market: str, line: float) -> dict | None:
    """Exact lookup for the re-check step. No fuzzy matching."""
    for row in lines:
        if (str(row["game_id"]) == str(game_id) and row["player"] == player
                and row["market"] == market and float(row["line"]) == float(line)):
            return row
    return None
