"""Muse Prop1 — odds via PropLine (Odds API aggregator).
Replaces bovada.py (removed 2026-11-10, Grok review): NO code in Prop1 may
fetch from, log into, or place bets with Bovada or any other sportsbook.
The price source is PropLine's aggregate odds only — the fetch requests NO
bookmaker, and extraction uses the MEDIAN price across the bookmakers
PropLine returns, explicitly excluding "bovada". No single sportsbook's
price is ever used as the market price.

Bookmaker allowlist (config.yaml `odds_bookmakers`): if set, only those
bookmaker keys are used (e.g. ["kalshi", "polymarket"] once PropLine's keys
for them are verified against a real response — NOT verified as of
2026-11-10; do not assume the keys). If empty (default), the median across
all non-Bovada bookmakers is used. Set the allowlist to restrict to
Kalshi/Polymarket once their PropLine keys are confirmed.

PropLine response shape (verified against docs 2026-11-06, the-odds-api compatible):
  bulk: {"events": [{"id", "sport_key", "home_team", "away_team", "commence_time",
                     "bookmakers": [{"key": "bovada", "markets": [
                       {"key": "player_pass_yds", "last_update": "...",
                        "suspended_at": null, "line_type": "main",
                        "outcomes": [{"name": "Over", "description": "Josh Allen",
                                      "price": -110, "point": 245.5, "player_id": "espn:123"}]}]}]}]}
  single-event: same, without the outer "events" wrapper.
  Anytime TD: outcomes are Yes/No, point is usually null -> treat as 0.5.

Quota: every fetch reserves against the PropLine budget (quota.py) before
the request is made. Over budget -> QuotaExceeded, no request is sent.
Requires PROPLINE_API_KEY in env. No key = raises, never silently falls back
to stale lines.
"""
from __future__ import annotations
import os, statistics, time
import requests

from quota import QuotaExceeded, QuotaTracker

BASE = "https://api.prop-line.com/v1"

# Bovada is banned from this module entirely. The exclusion is explicit so a
# future bookmaker list cannot silently re-admit it.
BANNED_BOOKMAKERS = {"bovada"}

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
# are tested, but extract_propline_lines is player-centric (it requires a
# `description` player name on outcomes) and the PropLine team-lines payload
# shape is unverified — no key on file. Do NOT add these to MARKETS until a
# real PropLine team-lines response has been inspected; guessing the shape
# would silently drop or misread lines.
TEAM_MARKETS = {
    "ncaab": ["team_points", "team_rebounds", "team_assists", "team_threes"],
    "ncaaw": ["team_points", "team_rebounds", "team_assists", "team_threes"],
}

# Module-level configuration, set by the runner from config.yaml.
# _BOOKMAKERS: allowlist of PropLine bookmaker keys, or None/[] for
# "median across all non-Bovada bookmakers". _QUOTA: QuotaTracker or None
# (None = no enforcement; tests and dry-run rely on this).
_BOOKMAKERS: list[str] | None = None
_QUOTA: QuotaTracker | None = None


def configure_bookmakers(bookmakers: list[str] | None) -> None:
    """Set the bookmaker allowlist. None/[] = median across all non-Bovada
    bookmakers. Any key in BANNED_BOOKMAKERS is dropped silently — the
    allowlist can never re-admit a banned book."""
    global _BOOKMAKERS
    if not bookmakers:
        _BOOKMAKERS = None
    else:
        _BOOKMAKERS = [b for b in bookmakers if b not in BANNED_BOOKMAKERS]


def configure_quota(tracker: QuotaTracker | None) -> None:
    """Set the quota tracker enforced by fetch_propline_props."""
    global _QUOTA
    _QUOTA = tracker


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


def fetch_propline_props(sport: str, api_key: str | None = None) -> dict:
    """Return raw bulk odds payload + fetch timestamp for one sport.
    No bookmaker filter is requested — PropLine returns all bookmakers and
    extraction decides which prices count. The call is reserved against the
    quota BEFORE the request; over budget raises QuotaExceeded and no
    request is sent."""
    if sport not in SPORT_KEYS:
        raise ValueError(f"unknown sport {sport}, expected one of {list(SPORT_KEYS)}")
    if _QUOTA is not None:
        _QUOTA.reserve(1)
    markets = ",".join(MARKETS[sport])
    url = f"{BASE}/sports/{SPORT_KEYS[sport]}/odds"
    r = requests.get(url, params={"apiKey": _api_key(api_key), "markets": markets},
                     timeout=15)
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


def _allowed_bookmaker(key: str | None) -> bool:
    if not key:
        return False
    if key in BANNED_BOOKMAKERS:
        return False
    if _BOOKMAKERS:
        return key in _BOOKMAKERS
    return True


def _median_price(prices: list[int]) -> int:
    """Median American-odds price across contributing bookmakers, rounded to
    an integer (American odds are integers). A single bookmaker's price is
    returned unchanged."""
    return int(round(statistics.median(prices)))


def extract_propline_lines(payload: dict, sport: str = "nfl") -> list[dict]:
    """Flatten to exact (game_id, player, market, line, over_price, under_price).
    Exact matching only — a 245.5 line is never treated as 249.5.
    Only line_type == 'main' rows are used; alternate lines are ignored in v1.
    Suspended markets (suspended_at set) are skipped.
    Price rule: for each (game, market, player, point), over_price and
    under_price are the MEDIAN across the allowed bookmakers that posted
    that outcome. "bovada" is never an allowed bookmaker. The row records
    which bookmakers contributed ("bookmakers") so the price source is
    auditable in the log.
    Returns [] on an unexpected shape — never guesses a line.
    """
    as_of = payload.get("as_of") if isinstance(payload, dict) else None
    grouped: dict[tuple, dict] = {}
    for event in _events_from(payload):
        game_id = str(event.get("id", ""))
        for book in event.get("bookmakers", []) or []:
            if not _allowed_bookmaker(book.get("key")):
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
                        "over_prices": [], "under_prices": [],
                        "bookmakers": set(),
                        "last_update": market.get("last_update"), "as_of": as_of,
                    })
                    if side == "over":
                        row["over_prices"].append(price)
                    else:
                        row["under_prices"].append(price)
                    row["bookmakers"].add(book.get("key"))
                    if oc.get("player_id"):
                        row["player_id"] = oc.get("player_id")
    out = []
    for r in grouped.values():
        if not r["over_prices"] or not r["under_prices"]:
            continue
        out.append({
            "sport": r["sport"], "game_id": r["game_id"],
            "home_team": r["home_team"], "away_team": r["away_team"],
            "commence_time": r["commence_time"],
            "player": r["player"], "player_id": r["player_id"],
            "market": r["market"], "line": r["line"],
            "over_price": _median_price(r["over_prices"]),
            "under_price": _median_price(r["under_prices"]),
            "bookmakers": sorted(r["bookmakers"]),
            "last_update": r["last_update"], "as_of": r["as_of"],
        })
    return out


def find_line(lines: list[dict], game_id: str, player: str, market: str, line: float) -> dict | None:
    """Exact lookup for the re-check step. No fuzzy matching."""
    for row in lines:
        if (str(row["game_id"]) == str(game_id) and row["player"] == player
                and row["market"] == market and float(row["line"]) == float(line)):
            return row
    return None
