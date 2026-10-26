#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from live_scoreboard_bridge import find_matching_game

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
SPORT_KEY = "basketball_ncaab"

START_DATE = "2025-03-20"
END_DATE = "2025-03-23"

OUTPUT_FILE = "backtest_data.jsonl"

#############################################

def iter_dates(start, end):
    start = datetime.strptime(start, "%Y-%m-%d")
    end = datetime.strptime(end, "%Y-%m-%d")
    while start <= end:
        yield start
        start += timedelta(days=1)

def make_snapshot(d):
    dt = datetime(d.year, d.month, d.day, 16, 0, 0, tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

#############################################

async def fetch_odds(session, d):
    url = f"https://api.the-odds-api.com/v4/historical/sports/{SPORT_KEY}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
        "date": make_snapshot(d),
    }

    async with session.get(url, params=params) as r:
        return await r.json()

#############################################

async def fetch_scoreboard(session, d):
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    params = {"dates": d.strftime("%Y%m%d")}

    async with session.get(url, params=params) as r:
        data = await r.json()

    games = []

    for event in data.get("events", []):
        comp = event["competitions"][0]
        teams = comp["competitors"]

        home = next(t for t in teams if t["homeAway"] == "home")
        away = next(t for t in teams if t["homeAway"] == "away")

        games.append({
            "home_team": home["team"]["displayName"],
            "away_team": away["team"]["displayName"],
            "home_score": int(home["score"]),
            "away_score": int(away["score"]),
            "completed": comp["status"]["type"]["completed"]
        })

    return games

#############################################

def extract_market(event):
    m = {
        "market_total": 150.5,
        "home_ml_odds": -110,
        "away_ml_odds": -110,
        "home_spread_line": -3.5,
        "away_spread_line": 3.5,
    }

    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):

            if market["key"] == "totals":
                for o in market["outcomes"]:
                    if o["name"] == "Over":
                        m["market_total"] = o["point"]

            if market["key"] == "h2h":
                for o in market["outcomes"]:
                    if o["name"] == event["home_team"]:
                        m["home_ml_odds"] = o["price"]
                    elif o["name"] == event["away_team"]:
                        m["away_ml_odds"] = o["price"]

            if market["key"] == "spreads":
                for o in market["outcomes"]:
                    if o["name"] == event["home_team"]:
                        m["home_spread_line"] = o["point"]
                    elif o["name"] == event["away_team"]:
                        m["away_spread_line"] = o["point"]

    return m

#############################################

def build_row(event, game):
    market = extract_market(event)

    payload = {
        "home_team": event["home_team"],
        "away_team": event["away_team"],

        # 🔑 CRITICAL FIX (no leakage)
        "home_score": 0,
        "away_score": 0,
        "minutes_left": 20,
        "seconds_left": 0,
        "mode": "1h",
        "completed": False,
        "status_detail": "PREGAME",

        **market
    }

    return {
        "payload": payload,
        "final_home_score": game["home_score"],
        "final_away_score": game["away_score"]
    }

#############################################

async def main():
    rows = []

    async with aiohttp.ClientSession() as session:
        for d in iter_dates(START_DATE, END_DATE):

            print(f"\n=== {d.date()} ===")

            odds = await fetch_odds(session, d)
            events = odds.get("data", [])

            games = await fetch_scoreboard(session, d)

            matched = 0

            for e in events:
                match = find_matching_game(
                    games,
                    e["home_team"],
                    e["away_team"]
                )

                if not match:
                    continue

                if not match["completed"]:
                    continue

                rows.append(build_row(e, match))
                matched += 1

            print(f"matched_rows: {matched}")

    with open(OUTPUT_FILE, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    print(f"\nSaved {len(rows)} rows")

#############################################

asyncio.run(main())
