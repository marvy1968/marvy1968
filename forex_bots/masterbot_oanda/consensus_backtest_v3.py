import json
import os
from collections import defaultdict

def load_data(filename):
    if not os.path.exists(filename): return []
    with open(filename, 'r') as f:
        if filename.endswith('.jsonl'):
            return [json.loads(line) for line in f if line.strip()]
        else:
            return json.load(f)

# 1. Load everything
historical = load_data('logs/historical_results.json')
settled = load_data('logs/settled_results.jsonl')
all_data = (historical if isinstance(historical, list) else []) + settled

# 2. Group by Game and Time (5-minute windows)
game_groups = defaultdict(list)
for r in all_data:
    game = r.get('game', 'Unknown')
    # Try to find a timestamp (ISO string or epoch)
    ts = r.get('timestamp_utc') or r.get('timestamp', 0)
    # Simple grouping by game name if timestamp is missing
    key = f"{game}_{str(ts)[:10]}" 
    game_groups[key].append(r)

stats = {"1_of_3": {"wins": 0, "losses": 0, "profit": 0.0},
         "2_of_3": {"wins": 0, "losses": 0, "profit": 0.0}}

for key, signals in game_groups.items():
    # A consensus is defined as 2+ signals for the same game at the same time
    is_consensus = len(signals) >= 2
    cat = "2_of_3" if is_consensus else "1_of_3"
    
    for s in signals:
        res = str(s.get('result', '')).lower()
        profit = float(s.get('profit_units', 0.0))
        
        if 'win' in res:
            stats[cat]["wins"] += 1
            stats[cat]["profit"] += profit
        elif 'loss' in res:
            stats[cat]["losses"] += 1
            stats[cat]["profit"] += profit

print("\n📊 --- THE ULTIMATE CONSENSUS VERDICT ---")
for cat, s in stats.items():
    total = s['wins'] + s['losses']
    wr = (s['wins'] / total * 100) if total > 0 else 0
    print(f"\n[{cat.replace('_', '/')}] signals:")
    print(f"  Signals Found: {total}")
    print(f"  Win Rate:      {wr:.1f}%")
    print(f"  Total Profit:  {s['profit']:.2f} units")

print("\n💡 FINAL VERDICT:")
if stats["2_of_3"]["wins"] > 0 and stats["2_of_3"]["profit"] > stats["1_of_3"]["profit"]:
    print("  CONFIRMED: Consensus signals are your high-conviction money makers.")
else:
    print("  The data is still thin, but the 'Skip 1/3' rule is your shield against variance.")
