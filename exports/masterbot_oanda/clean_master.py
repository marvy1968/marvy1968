import time
import requests
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ODDS_API_KEY")
SPORT = "basketball_nba"
MARKETS = ["team_totals", "player_points"]

def fetch_safe_props():
    try:
        events_response = requests.get(f"https://api.the-odds-api.com/v4/sports/{SPORT}/events", params={"apiKey": API_KEY})
        if events_response.status_code != 200: return
        events = events_response.json()
        if not events: return

        print(f"--- SCANNING {len(events)} EVENTS ---")
        for event in events:
            # We are simplifying logic here to bypass the broken deduper/telegram modules
            print(f"Checking: {event['away_team']} @ {event['home_team']}")
            
    except Exception as e:
        print(f"Error fetching: {e}")

print("🚀 Clean Master Started - Bypassing Telegram")
while True:
    fetch_safe_props()
    time.sleep(300) # Scan every 5 minutes
