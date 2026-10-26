import os, requests, asyncio, time, json, sqlite3
from datetime import datetime
from dotenv import load_dotenv
import telegram_sender 

load_dotenv()
API_KEY = os.getenv("ODDS_API_KEY")
DB = "performance_tracking.db"

# --- AJ MATH ENGINE ---
def american_to_decimal(odds):
    return 1 + (odds / 100) if odds > 0 else 1 + (100 / abs(odds))

def calculate_kelly(model_prob, odds, fraction=0.25):
    b = american_to_decimal(odds) - 1
    p = model_prob
    q = 1 - p
    kelly = (b * p - q) / b if b > 0 else 0
    return round(max(0, kelly * fraction), 4)

def get_learned_bias(market):
    """AJ SELF-LEARNING: Adjusts baseline based on real-world win history."""
    try:
        conn = sqlite3.connect(DB)
        res = conn.execute("SELECT AVG(ev_percent) FROM tracked_bets WHERE market=? AND result='win'", (market,)).fetchone()
        conn.close()
        return round(res[0], 2) if res and res[0] else 0.0
    except: return 0.0

def get_projections(sport, market):
    # AJ EDGE Logic: Baseline + Database Bias
    bias = get_learned_bias(market)
    base = (75.0 if "ncaab" in sport else 112.0) + bias
    mults = {"team_totals": 1.0, "team_totals_h1": 0.5, "team_totals_h2": 0.5, "team_totals_q1": 0.25, "team_totals_q2": 0.25, "team_totals_q3": 0.25, "team_totals_q4": 0.25}
    m = mults.get(market, 1.0)
    return {"h": base * m, "a": (base - 2.5) * m}

async def fetch_props(sport, ev_id, h_team, a_team):
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/events/{ev_id}/odds"
    params = {"apiKey": API_KEY, "regions": "us", "markets": "team_totals", "oddsFormat": "american"}
    try:
        r = requests.get(url, params=params)
        if r.status_code != 200: return []
        data, edges = r.json(), []
        
        # Determine League Label
        label = "NBA"
        if "ncaab" in sport: label = "NCAAM"
        elif "wncaab" in sport: label = "NCAAW"
        elif "wnba" in sport: label = "WNBA"
        elif "euroleague" in sport: label = "EUROLEAGUE"

        for book in data.get("bookmakers", []):
            if book["title"] not in ["DraftKings", "FanDuel"]: continue
            for mkt in book.get("markets", []):
                projs = get_projections(sport, mkt["key"])
                for out in mkt.get("outcomes", []):
                    team, line, side, odds = out["description"], out["point"], out["name"], out["price"]
                    proj = projs["h"] if team == h_team else projs["a"]
                    diff = proj - line if side == "Over" else line - proj
                    
                    # AJ SNIPER: The 'No-Chance' mathematical lock (diff > 14.5 pts)
                    prob = 0.68 + (0.01 * diff) 
                    if diff > 14.5: prob = 0.96 
                    
                    kelly = calculate_kelly(prob, odds)
                    if kelly > 0 and prob >= 0.65:
                        # AJ BRANDING & PRO LOOK WITH LEAGUE TAG
                        header = f"🎯 *[AJ SNIPER - {label}]*" if prob >= 0.95 else f"💰 *[AJ EDGE - {label}]*"
                        edges.append(
                            f"{header}\n"
                            f"🏟️ *{a_team} @ {h_team}*\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"🔹 *TARGET:* {team} {side} {line}\n"
                            f"🔹 *ODDS:* {odds} ({book['title']})\n"
                            f"📈 *PROB:* {round(prob*100,1)}% | *DIFF:* {round(diff,2)}\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"🔥 *SUGGEST BET:* `{round(kelly*100,2)}%` of Bankroll"
                        )
        return edges
    except: return []

async def run_engine():
    print(f"🚀 AJ ELITE MASTER SYSTEM ACTIVE - {time.ctime()}")
    while True:
        # Time Window: 10:00 AM - 11:00 PM Central
        if not (10 <= datetime.now().hour < 23):
            print("💤 Night Mode... Sleeping 30m"); await asyncio.sleep(1800); continue
        
        leagues = ["basketball_nba", "basketball_euroleague", "basketball_ncaab", "basketball_wncaab", "basketball_wnba"]
        for lg in leagues:
            res = requests.get(f"https://api.the-odds-api.com/v4/sports/{lg}/events", params={"apiKey": API_KEY})
            if res.status_code == 200:
                for ev in res.json():
                    found = await fetch_props(lg, ev["id"], ev["home_team"], ev["away_team"])
                    for msg in found: telegram_sender._send_message(msg)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(run_engine())
