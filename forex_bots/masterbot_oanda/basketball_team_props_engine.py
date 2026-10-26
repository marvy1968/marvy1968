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
MIN_EDGE_THRESHOLD = 2.0  # Slightly lower threshold for shorter periods (Quarters)

# ROI BOOSTS (From your provided Learning File)
ROI_BOOSTS = {
    "basketball_wnba": 4,          # 38.7% ROI
    "basketball_euroleague": 5,    # 42.9% ROI
    "basketball_nba": 2
}

# ALL AVAILABLE TEAM PROP MARKETS
TEAM_MARKETS = [
    "team_totals",      # Full Game
    "team_totals_h1",   # 1st Half
    "team_totals_h2",   # 2nd Half
    "team_totals_q1",   # 1st Quarter
    "team_totals_q2",   # 2nd Quarter
    "team_totals_q3",   # 3rd Quarter
    "team_totals_q4"    # 4th Quarter
]

def get_model_projections(home_team, away_team, market_key):
    """
    HOOK: Replace with your actual model logic.
    For now, it uses placeholders for different periods.
    """
    multipliers = {
        "team_totals": 1.0, "team_totals_h1": 0.5, "team_totals_h2": 0.5,
        "team_totals_q1": 0.25, "team_totals_q2": 0.25, "team_totals_q3": 0.25, "team_totals_q4": 0.25
    }
    m = multipliers.get(market_key, 1.0)
    return {"home_proj": 112.0 * m, "away_proj": 108.0 * m}

async def fetch_all_team_props(sport_key, event_id, home_team, away_team):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": ",".join(TEAM_MARKETS), # Request all periods at once
        "oddsFormat": "american"
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code != 200: return []
        
        data = response.json()
        boost = ROI_BOOSTS.get(sport_key, 0)
        edges = []

        for book in data.get("bookmakers", []):
            if book["title"] not in TARGET_BOOKS: continue
            
            for market in book.get("markets", []):
                market_key = market["key"]
                period_label = market_key.replace("team_totals", "GAME").replace("_", " ").upper()
                
                # Get specific model projection for this period
                projections = get_model_projections(home_team, away_team, market_key)
                
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
                            "period": period_label,
                            "team": team,
                            "bet": f"{side} {line}",
                            "edge": round(diff, 2),
                            "conf": round(65 + (diff * 2) + boost, 1),
                            "bookie": book["title"],
                            "odds": odds
                        })
        return edges
    except Exception as e:
        print(f"Error fetching {event_id}: {e}")
        return []

async def run_engine():
    print(f"🚀 ALL-PERIOD Team Prop Engine Active... {time.ctime()}")
    while True:
        for league in LEAGUES:
            events_url = f"https://api.the-odds-api.com/v4/sports/{league}/events"
            res = requests.get(events_url, params={"apiKey": API_KEY})
            if res.status_code != 200: continue
            
            for event in res.json():
                found_edges = await fetch_all_team_props(league, event["id"], event["home_team"], event["away_team"])
                for edge in found_edges:
                    print(f"\n💰 [EDGE FOUND] {edge['game']} ({edge['period']})")
                    print(f"   Target: {edge['team']} {edge['bet']} ({edge['odds']})")
                    print(f"   Edge: {edge['edge']} pts | Confidence: {edge['conf']}%")
                    print(f"   Book: {edge['bookie']}")
        print(f"\n--- Cycle Complete. {time.ctime()} ---")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(run_engine())

