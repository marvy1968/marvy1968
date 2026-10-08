"""Player prop lines from The Odds API (current and historical), one game at a time.

Props are only available per event. Costs (The Odds API pricing): current event odds use 1 credit per
market per region; historical event odds 10 credits per market per region. Responses are cached on
disk, so a backtest never pays twice for the same game.
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)
BASE = "https://api.the-odds-api.com/v4"


class Budget:
    """Stops paid calls once a credit cap is reached."""

    def __init__(self, cap: int):
        self.cap, self.used, self.remaining = cap, 0, None

    def charge(self, resp: requests.Response, estimate: int) -> None:
        self.used += int(resp.headers.get("x-requests-last") or estimate)
        self.remaining = resp.headers.get("x-requests-remaining", self.remaining)

    def allows(self, estimate: int) -> bool:
        return self.used + estimate <= self.cap


def _get(path: str, params: dict) -> requests.Response:
    resp = requests.get(f"{BASE}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp


def events(api_key: str, sport: str) -> list[dict]:
    """Upcoming events (free call)."""
    return _get(f"/sports/{sport}/events", {"apiKey": api_key}).json()


def event_props(api_key: str, sport: str, event_id: str, markets: list[str], bookmakers: str,
                budget: Budget | None = None) -> dict:
    est = len(markets)
    if budget and not budget.allows(est):
        raise RuntimeError("credit cap reached")
    resp = _get(f"/sports/{sport}/events/{event_id}/odds", {
        "apiKey": api_key, "markets": ",".join(markets), "bookmakers": bookmakers, "oddsFormat": "american"})
    if budget:
        budget.charge(resp, est)
    return resp.json()


def historical_events(api_key: str, sport: str, date_iso: str, cache: Path, budget: Budget) -> list[dict]:
    path = cache / f"events_{sport}_{date_iso[:13]}.json"
    if path.exists():
        return json.loads(path.read_text())
    if not budget.allows(1):
        raise RuntimeError("credit cap reached")
    resp = _get(f"/historical/sports/{sport}/events", {"apiKey": api_key, "date": date_iso})
    budget.charge(resp, 1)
    data = resp.json().get("data", [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def historical_event_props(api_key: str, sport: str, event_id: str, date_iso: str, markets: list[str],
                           bookmakers: str, cache: Path, budget: Budget) -> dict:
    path = cache / f"props_{event_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    est = 10 * len(markets)
    if not budget.allows(est):
        raise RuntimeError("credit cap reached")
    resp = _get(f"/historical/sports/{sport}/events/{event_id}/odds", {
        "apiKey": api_key, "date": date_iso, "markets": ",".join(markets), "bookmakers": bookmakers,
        "oddsFormat": "american"})
    budget.charge(resp, est)
    data = resp.json().get("data", {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def prop_rows(event: dict, prefer: str = "bovado") -> pd.DataFrame:
    """Flatten an event's props into one row per player/market/book with the Over and Under prices.
    The player is in `description`; the outcome `name` is just Over/Under."""
    rows = {}
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            for o in market.get("outcomes", []):
                player, side = o.get("description"), o.get("name")
                if not player or side not in ("Over", "Under") or o.get("point") is None:
                    continue
                key = (book["key"], market["key"], player)
                r = rows.setdefault(key, {"book": book["key"], "book_title": book.get("title", book["key"]),
                                          "market": market["key"], "player": player, "line": float(o["point"]),
                                          "home_team": event.get("home_team"), "away_team": event.get("away_team"),
                                          "commence_time": event.get("commence_time"), "event_id": event.get("id")})
                r["over_price" if side == "Over" else "under_price"] = o.get("price")
    df = pd.DataFrame(rows.values())
    if df.empty:
        return df
    # One line per player/market: the preferred book if it posted one, otherwise the most common line.
    df["_pref"] = (df["book"] != prefer).astype(int)
    return df.sort_values("_pref").drop_duplicates(["market", "player"]).drop(columns="_pref")


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


def norm_name(name: str) -> str:
    n = str(name).lower().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(_SUFFIX.sub("", n).split())
