#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import os
from dotenv import load_dotenv

from live_scoreboard_bridge import fetch_all_scoreboard_games, find_matching_game

load_dotenv(dotenv_path=".env")

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
SPORT_KEY = os.getenv("SPORT_KEY", "basketball_ncaab")
OUTPUT_FILE = "backtest_data.jsonl"

async def fetch_odds():
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

def extract_market(event):
    market = {
        "market_total": 150.0,
        "over_odds": -110,
        "under_odds": -110,
        "home_ml_odds": -110,
        "away_ml_odds": -110,
        "home_spread_line": -3.5,
        "away_spread_line": 3.5,
        "home_spread_odds": -110,
        "away_spread_odds": -110,
    }

    home = event.get("home_team")
    away = event.get("away_team")

    for book in event.get("bookmakers", []):
        for m in book.get("markets", []):
            key = m.get("key")

            if key == "h2h":
                for o in m.get("outcomes", []):
                    if o.get("name") == home:
                        market["home_ml_odds"] = o.get("price", -110)
                    elif o.get("name") == away:
                        market["away_ml_odds"] = o.get("price", -110)

            elif key == "spreads":
                for o in m.get("outcomes", []):
                    if o.get("name") == home:
                        market["home_spread_line"] = o.get("point", -3.5)
                        market["home_spread_odds"] = o.get("price", -110)
                    elif o.get("name") == away:
                        market["away_spread_line"] = o.get("point", 3.5)
                        market["away_spread_odds"] = o.get("price", -110)

            elif key == "totals":
                over = None
                under = None
                for o in m.get("outcomes", []):
                    nm = str(o.get("name", "")).lower()
                    if nm == "over":
                        over = o
                    elif nm == "under":
                        under = o
                if over:
                    market["market_total"] = over.get("point", 150.0)
                    market["over_odds"] = over.get("price", -110)
                if under:
                    market["under_odds"] = under.get("price", -110)

    return market

def build_row(event, scoreboard):
    market = extract_market(event)

    payload = {
        "home_team": event["home_team"],
        "away_team": event["away_team"],
        "home_score": scoreboard["home_score"],
        "away_score": scoreboard["away_score"],
        "minutes_left": 0,
        "seconds_left": 0,
        "mode": "2h",
        "completed": True,
        "status_detail": "FINAL",
        **market,
    }

    return {
        "payload": payload,
        "final_home_score": scoreboard["home_score"],
        "final_away_score": scoreboard["away_score"],
    }

async def main():
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing in environment")

    print("Fetching scoreboard...")
    scoreboard_games = await fetch_all_scoreboard_games()
    print(f"scoreboard_games: {len(scoreboard_games)}")

    print("Fetching odds...")
    odds_data = await fetch_odds()
    print(f"odds_events: {len(odds_data)}")

    rows = []

    for event in odds_data:
        home = event.get("home_team")
        away = event.get("away_team")

        match = find_matching_game(scoreboard_games, home, away)
        if not match:
            continue
        if not match.get("completed"):
            continue

        rows.append(build_row(event, match))

    print(f"Matched rows: {len(rows)}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    print("Saved → backtest_data.jsonl")

if __name__ == "__main__":
    asyncio.run(main())
