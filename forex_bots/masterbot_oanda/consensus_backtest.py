import json
import os
from collections import defaultdict

def load_jsonl(filename):
    data = []
    if not os.path.exists(filename): return data
    with open(filename, 'r') as f:
        for line in f:
            try: data.append(json.loads(line))
            except: continue
    return data

# Load our primary data sources
std_bets = load_jsonl('logs/marchedge_bets.jsonl')
v3_bets = load_jsonl('logs/team_props_bets.jsonl')
results = load_jsonl('logs/settled_results.jsonl')

# Map results by Game Name for easy lookup
outcome_map = {r.get('game'): r.get('result') for r in results}

# Group all bets by Game + Approximate Time (within 5 mins) to find consensus
all_bet_events = defaultdict(list)
for b in std_bets + v3_bets:
    # Round timestamp to nearest 5 mins to group simultaneous alerts
    time_bucket = (b.get('timestamp', 0) // 300) * 300
    key = f"{b.get('game')}_{time_bucket}"
    all_bet_events[key].append(b)

stats = {"1_of_3": {"wins": 0, "losses": 0, "profit": 0.0},
         "2_of_3": {"wins": 0, "losses": 0, "profit": 0.0}}

for key, bets in all_bet_events.items():
    game_name = bets[0].get('game')
    outcome = outcome_map.get(game_name)
    
    if not outcome: continue # Skip if game isn't settled yet
    
    # Determine if this was a 1/3 (Solo) or 2/3 (Consensus) signal
    category = "2_of_3" if len(bets) >= 2 else "1_of_3"
    
    for b in bets:
        is_win = (b.get('side').lower() == outcome.lower())
        odds = b.get('odds', -110)
        # Standard $100 unit for backtesting math
        payout = 100 * (100/abs(odds)) if odds < 0 else 100 * (odds/100)
        
        if is_win:
            stats[category]["wins"] += 1
            stats[category]["profit"] += payout
        else:
            stats[category]["losses"] += 1
            stats[category]["profit"] -= 100

print("\n📊 --- CONSENSUS BACKTEST RESULTS (Last 30 Days) ---")
for cat, s in stats.items():
    total = s['wins'] + s['losses']
    win_rate = (s['wins'] / total * 100) if total > 0 else 0
    print(f"\n[{cat.replace('_', '/')}] SIGNALS:")
    print(f"  Total Bets: {total}")
    print(f"  Win Rate:   {win_rate:.1f}%")
    print(f"  Net Profit: ${s['profit']:.2f}")

profit_diff = stats["2_of_3"]["profit"] - (stats["1_of_3"]["profit"] + stats["2_of_3"]["profit"])
print(f"\n💡 IMPACT: By skipping 1/3 signals, your P/L would change by: ${abs(profit_diff):.2f}")
