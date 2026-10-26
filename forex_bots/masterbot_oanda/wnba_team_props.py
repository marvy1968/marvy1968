#!/usr/bin/env python3
import os, requests, time, sys, random
from datetime import datetime
from dotenv import load_dotenv
from betting_math import enrich_signal, implied_probability
from edge_alert_deduper import deduper

load_dotenv()
API_KEY = os.getenv("ODDS_API_KEY")
SPORT = "basketball_wnba"
CONFIDENCE_FLOOR = float(sys.argv[1]) if len(sys.argv) > 1 else 85.0
MARKETS = ["team_totals","player_points","player_rebounds","player_assists","player_points_rebounds_assists","player_threes"]

def fetch_and_enrich_wnba_props():
    print(f"📡 [AJ SNIPER] Fetching WNBA Props (totals+rebounds+assists+PRA) — Floor: {CONFIDENCE_FLOOR}%")
    events_response = requests.get(f"https://api.the-odds-api.com/v4/sports/{SPORT}/events", params={"apiKey": API_KEY})
    if events_response.status_code != 200: return
    events = events_response.json()
    if not events: return
    print(f"✅ Found {len(events)} WNBA games.\n")
    alerts = []
    for event in events:
        event_id = event["id"]
        home = event["home_team"]
        away = event["away_team"]
        game = f"{away} @ {home}"
        props_response = requests.get(f"https://api.the-odds-api.com/v4/sports/{SPORT}/events/{event_id}/odds", params={"apiKey": API_KEY, "regions": "us", "markets": ",".join(MARKETS), "oddsFormat": "american"})
        if props_response.status_code != 200: continue
        game_data = props_response.json()
        for bookmaker in game_data.get("bookmakers", []):
            if bookmaker["title"] not in ["DraftKings","FanDuel","Bovada"]: continue
            for market in bookmaker.get("markets", []):
                for outcome in market.get("outcomes", []):
                    team = outcome.get("description") or outcome.get("name", "Unknown")
                    side = outcome.get("name", "")
                    line = float(outcome.get("point", 0))
                    odds = float(outcome.get("price", 0))
                    implied = implied_probability(odds)
                    home_advantage = 0.042 if team == home else -0.028
                    variance = random.uniform(-0.018, 0.018)
                    model_prob = max(0.05, min(0.95, implied + home_advantage + variance))
                    signal = {"model_prob": model_prob, "odds": odds}
                    enriched = enrich_signal(signal)
                    edge = enriched["edge"]
                    kelly = enriched["kelly_quarter"]
                    diff = round(edge, 1)
                    if diff < 3.0 or (model_prob * 100) < CONFIDENCE_FLOOR: continue
                    target_label = f"{team} {side} {line}" if line else f"{team} {side}"
                    alert_dict = {"game": game, "league": "WNBA", "target": target_label, "odds": odds, "book": bookmaker["title"], "prob": round(model_prob*100,1), "diff": diff, "kelly": round(kelly*100,2)}
                    if deduper.should_show(game, alert_dict["target"], line, diff):
                        alerts.append(alert_dict)
    deduper.clear_old()
    telegram_text = deduper.format_telegram(alerts)
    print(telegram_text if telegram_text else f"🟡 [AJ SNIPER] No strong WNBA edges above {CONFIDENCE_FLOOR}% this run.")

if __name__ == "__main__":
    print(f"🚀 WNBA Props Sniper started — {datetime.now().strftime('%H:%M')}")
    while True:
        fetch_and_enrich_wnba_props()
        time.sleep(180)
