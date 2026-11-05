import numpy as np
import pandas as pd
from backtest_v3_3_institutional import all_trades

df = pd.DataFrame(all_trades)
if df.empty:
    print("No trade records found in backtest_v3_3_institutional.")
    exit()

def get_metrics(trades_df):
    if len(trades_df) == 0:
        return {"n": 0, "wr": 0.0, "net_r": 0.0, "pf": 0.0, "exp": 0.0, "avg_w": 0.0, "avg_l": 0.0, "max_dd": 0.0, "eff": 0.0}
    r = trades_df['raw_r'].values
    wins = r[r > 0]
    losses = r[r <= 0]
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(cum)
    max_dd = np.max(peak - cum) if len(cum) > 0 else 0.0
    
    wr = (len(wins) / len(r)) * 100.0
    pf = abs(wins.sum() / (losses.sum() + 1e-9))
    exp = r.mean()
    net_r = r.sum()
    avg_w = wins.mean() if len(wins) > 0 else 0.0
    avg_l = losses.mean() if len(losses) > 0 else 0.0
    eff = net_r / max_dd if max_dd > 0 else 0.0
    
    return {
        "n": len(r), "wr": wr, "net_r": net_r, "pf": pf,
        "exp": exp, "avg_w": avg_w, "avg_l": avg_l, "max_dd": max_dd, "eff": eff
    }

print("=" * 110)
print(" 🔬 V3.3 ROBUSTNESS DESTRUCTION & OUTLIER EXCLUSION TEST")
print("=" * 110)
print(f"{'Test Scenario':<26} | {'Trades':<7} | {'Win Rate':<9} | {'Net R':<11} | {'PF':<6} | {'Exp/Tr':<10} | {'Avg W / L':<14} | {'Max DD':<8} | {'Harvest/DD'}")
print("-" * 110)

scenarios = {
    "1. Baseline (All Trades)": df,
    "2. Without CVX":           df[df['symbol'] != 'CVX'],
    "3. Without XOM":           df[df['symbol'] != 'XOM'],
    "4. Without LLY":           df[df['symbol'] != 'LLY'],
    "5. Without Energy (No XLE)":df[~df['symbol'].isin(['CVX', 'XOM'])],
    "6. Without Best 1 Trade":  df.sort_values('raw_r', ascending=True).iloc[:-1],
    "7. Without Best 3 Trades": df.sort_values('raw_r', ascending=True).iloc[:-3],
    "8. Without Best 5 Trades": df.sort_values('raw_r', ascending=True).iloc[:-5],
}

for name, subset in scenarios.items():
    m = get_metrics(subset)
    w_l_str = f"{m['avg_w']:+.2f} / {m['avg_l']:+.2f}"
    print(f"{name:<26} | {m['n']:<7} | {m['wr']:5.1f}%   | {m['net_r']:+8.2f}R  | {m['pf']:4.2f} | {m['exp']:+6.2f}R/tr | {w_l_str:<14} | {m['max_dd']:5.2f}R  | {m['eff']:5.2f}")

print("=" * 110)

# PURE PERMUTATION MONTE CARLO (No replacement - exact same 37 trades shuffled 10,000 times)
print("\n" + "=" * 90)
print(" 🎲 PURE SEQUENCE SHUFFLING MONTE CARLO (10,000 Permutations / Zero Replacement)")
print("=" * 90)

all_r = df['raw_r'].values
N_SIMS = 10000
perm_dds = []
perm_streaks = []

np.random.seed(42)
for _ in range(N_SIMS):
    shuffled = np.random.permutation(all_r)
    cum = np.cumsum(shuffled)
    peak = np.maximum.accumulate(cum)
    perm_dds.append(np.max(peak - cum))
    
    is_loss = (shuffled <= 0).astype(int)
    s, m_s = 0, 0
    for v in is_loss:
        if v == 1:
            s += 1
            m_s = max(m_s, s)
        else:
            s = 0
    perm_streaks.append(m_s)

p_dd = np.array(perm_dds)
p_st = np.array(perm_streaks)

print(f"{'Metric':<30} | {'50th %ile (Median)':<20} | {'95th %ile (Tail)':<20} | {'99th %ile (Worst Case)'}")
print("-" * 90)
print(f"{'Max Drawdown (R)':<30} | {np.percentile(p_dd, 50):<19.2f}R | {np.percentile(p_dd, 95):<19.2f}R | {np.percentile(p_dd, 99):.2f}R")
print(f"{'Max Losing Streak':<30} | {int(np.percentile(p_st, 50)):<20} | {int(np.percentile(p_st, 95)):<20} | {int(np.percentile(p_st, 99))}")
print(f"{'Fixed Total Harvest (R)':<30} | {all_r.sum():<+19.2f}R | {all_r.sum():<+19.2f}R | {all_r.sum():+.2f}R")
print("-" * 90)
print(f"• Probability of Realized DD exceeding 8.0R in random sequences: {(p_dd > 8.0).sum() / N_SIMS * 100:.2f}%")
print(f"• Probability of Realized DD exceeding 10.0R in random sequences: {(p_dd > 10.0).sum() / N_SIMS * 100:.2f}%")
print("=" * 90)
