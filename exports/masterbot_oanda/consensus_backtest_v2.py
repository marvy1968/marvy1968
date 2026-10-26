import json
import os
from datetime import datetime
from collections import defaultdict

def load_jsonl(filename):
    data = []
    if not os.path.exists(filename): return data
    with open(filename, 'r') as f:
        for line in f:
            try: data.append(json.loads(line))
            except: continue
    return data

results = load_jsonl('logs/settled_results.jsonl')

# Grouping results by Game and Time window (5 mins) to find overlapping signals
game_groups = defaultdict(list)
for r in results:
    # Convert ISO timestamp to a grouping key
    try:
        ts = datetime.fromisoformat(r.get('timestamp_utc').replace('Z', '')).timestamp()
        time_bucket = (ts // 300) * 300
        key = f"{r.get('game')}_{time_bucket}"
        game_groups[key].append(r)
    except: continue

stats = {"1_of_3": {"wins": 0, "losses": 0, "profit": 0.0},
         "2_of_3": {"wins": 0, "losses": 0, "profit": 0.0}}

for key, bets in game_groups.items():
    # If we have multiple bets for the same game in the same window, it's a Consensus
    # We check unique 'tag' or 'market' to ensure it's different engines/strategies
    unique_signals = len(set([b.get('tag', 'std') + b.get('market', '') for b in bets]))
    
    category = "2_of_3" if unique_signals >= 2 else "1_of_3"
    
    for b in bets:
        profit = float(b.get('profit_units', 0.0))
        if b.get('result') == 'win':
            stats[category]["wins"] += 1
        elif b.get('result') == 'loss':
            stats[category]["losses"] += 1
        stats[category]["profit"] += profit

print("\n📊 --- REAL-WORLD CONSENSUS ANALYSIS ---")
for cat, s in stats.items():
    total = s['wins'] + s['losses']
    win_rate = (s['wins'] / total * 100) if total > 0 else 0
    print(f"\n[{cat.replace('_', '/')}] SIGNALS:")
    print(f"  Total Bets: {total}")
    print(f"  Win Rate:   {win_rate:.1f}%")
    print(f"  P/L Units:  {s['profit']:.2f}")

print("\n💡 STRATEGY INSIGHT:")
if stats["1_of_3"]["profit"] < 0:
    print(f"  Your 1/3 signals are leaking {abs(stats['1_of_3']['profit']):.2f} units.")
    print("  By skipping them, you keep that money in your pocket.")
else:
    print("  Your solo signals are actually profitable! You might want to keep them but lower the stake.")
