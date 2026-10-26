import os
import requests
import asyncio
import time
from dotenv import load_dotenv

# Load API Key from your .env
load_dotenv()
API_KEY = os.getenv("ODDS_API_KEY")

# CONFIGURATION
LEAGUES = ["basketball_nba", "basketball_wnba", "basketball_euroleague"]
TARGET_BOOKS = ["DraftKings", "FanDuel", "Bovada"]
MIN_EDGE_THRESHOLD = 2.5  # Only alert if model is 2.5+ points off the line

# ROI BOOSTS (From your provided PDF/Data)
# WNBA: 38.7% ROI | EuroLeague: 42.9% ROI
ROI_BOOSTS = {
    "basketball_wnba": 4,
    "basketball_euroleague": 5,
    "basketball_nba": 2
}

def get_model_projections(home_team, away_team):
    """
    PLACEHOLDER: This is where your actual math/model goes.
    For now, it returns dummy data. Replace with your actual model call.
    """
    return {"home_proj": 110.5, "away_proj": 105.0}

async def fetch_team_totals(session, sport_key, event_id, home_team, away_team):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "team_totals",
        "oddsFormat": "american"
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code != 200: return []
        
        data = response.json()
        projections = get_model_projections(home_team, away_team)
        boost = ROI_BOOSTS.get(sport_key, 0)
        
        edges = []
        for book in data.get("bookmakers", []):
            if book["title"] not in TARGET_BOOKS: continue
            
            for market in book.get("markets", []):
                for outcome in market.get("outcomes", []):
                    team = outcome["description"]
                    line = outcome["point"]
                    side = outcome["name"]
                    odds = outcome["price"]
                    
                    proj = projections["home_proj"] if team == home_team else projections["away_proj"]
                    diff = proj - line if side == "Over" else line - proj
                    
                    if diff >= MIN_EDGE_THRESHOLD:
                        edges.append({
                            "game": f"{away_team} @ {home_team}",
                            "league": sport_key,
                            "team": team,
                            "bet": f"{side} {line}",
                            "edge": round(diff, 2),
                            "conf": round(65 + (diff * 2) + boost, 1), # Base 65% + diff + ROI Boost
                            "bookie": book["title"],
                            "odds": odds
                        })
        return edges
    except Exception as e:
        print(f"Error fetching event {event_id}: {e}")
        return []

async def run_engine():
    print(f"🚀 Basketball Team Prop Engine Active... {time.ctime()}")
    
    while True:
        for league in LEAGUES:
            # 1. Get Event IDs
            events_url = f"https://api.the-odds-api.com/v4/sports/{league}/events"
            events = requests.get(events_url, params={"apiKey": API_KEY}).json()
            
            if not events: continue
            
            for event in events:
                found_edges = await fetch_team_totals(None, league, event["id"], event["home_team"], event["away_team"])
                
                for edge in found_edges:
                    print(f"\n💰 [EDGE FOUND] {edge['game']}")
                    print(f"   Target: {edge['team']} {edge['bet']} ({edge['odds']})")
                    print(f"   Edge: {edge['edge']} pts | Confidence: {edge['conf']}%")
                    print(f"   Book: {edge['bookie']} | League: {edge['league']}")
        
        print("\n--- Cycle Complete. Sleeping 60s ---")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(run_engine())

