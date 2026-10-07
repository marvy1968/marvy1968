"""The Odds API (https://the-odds-api.com): consensus prices across US sportsbooks.

Optional. When ODDS_API_KEY is set, these odds replace the ones from ESPN/nflverse/CFBD.
Each call costs (markets x regions) credits; this client requests 3 markets in 1 region.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta
from statistics import median

import requests

from ..models import Game, Odds
from .teams import similarity

log = logging.getLogger(__name__)
URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"


def fetch(api_key: str, sport_key: str, regions: str = "us") -> list[dict]:
    resp = requests.get(URL.format(sport=sport_key), timeout=30, params={
        "apiKey": api_key, "regions": regions, "markets": "h2h,spreads,totals", "oddsFormat": "american"})
    resp.raise_for_status()
    log.info("Odds API %s: %s credits left", sport_key, resp.headers.get("x-requests-remaining"))
    return resp.json()


def _consensus_line(points: list[float]) -> float:
    """Most commonly posted line; ties go to the one nearest the median."""
    counts = Counter(points)
    mid = median(points)
    return max(counts, key=lambda p: (counts[p], -abs(p - mid)))


def consensus(event: dict) -> Odds:
    """Median line across books; price is the median price among books posting that line."""
    home, away = event["home_team"], event["away_team"]
    spreads, totals, mls = [], [], {"home": [], "away": [], "draw": []}
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            outs = {o["name"]: o for o in market.get("outcomes", [])}
            if market["key"] == "h2h":
                for side, name in (("home", home), ("away", away), ("draw", "Draw")):
                    if name in outs:
                        mls[side].append(outs[name]["price"])
            elif market["key"] == "spreads" and home in outs and away in outs:
                spreads.append((outs[home]["point"], outs[home]["price"], outs[away]["price"]))
            elif market["key"] == "totals" and "Over" in outs and "Under" in outs:
                totals.append((outs["Over"]["point"], outs["Over"]["price"], outs["Under"]["price"]))

    odds = Odds(provider=f"consensus of {len(event.get('bookmakers', []))} books")
    if spreads:
        odds.spread = _consensus_line([s[0] for s in spreads])
        at = [s for s in spreads if s[0] == odds.spread]
        odds.home_spread_price = median(s[1] for s in at)
        odds.away_spread_price = median(s[2] for s in at)
    if totals:
        odds.total = _consensus_line([t[0] for t in totals])
        at = [t for t in totals if t[0] == odds.total]
        odds.over_price = median(t[1] for t in at)
        odds.under_price = median(t[2] for t in at)
    if mls["home"] and mls["away"]:
        odds.home_ml, odds.away_ml = median(mls["home"]), median(mls["away"])
        odds.draw_ml = median(mls["draw"]) if mls["draw"] else None
    return odds


def attach(games: list[Game], events: list[dict], min_similarity: float = 0.75) -> int:
    """Replace each game's odds with the best-matching Odds API event. Returns games matched."""
    matched = 0
    for game in games:
        best, best_score = None, 0.0
        for ev in events:
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if abs(start - game.start) > timedelta(hours=12):
                continue
            score = min(similarity(game.home, ev["home_team"]), similarity(game.away, ev["away_team"]))
            if score > best_score:
                best, best_score = ev, score
        if best and best_score >= min_similarity:
            fresh = consensus(best)
            if game.odds:  # keep any opening lines the primary source had
                fresh.spread_open, fresh.total_open = game.odds.spread_open, game.odds.total_open
            game.odds = fresh
            matched += 1
    return matched
