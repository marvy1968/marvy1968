import numpy as np
import pandas as pd

# Load backtest trade records from V3.3 run
from backtest_v3_3_institutional import all_trades

df = pd.DataFrame(all_trades)
if df.empty:
    print("No trades found in backtest_v3_3_institutional.")
    exit()

df['is_win'] = df['raw_r'] > 0

print("=" * 90)
print(" 🔬 1. FORENSIC MAE / MFE DECOMPOSITION (N = 37 TRADES)")
print("=" * 90)

# A. Winners vs Losers
win_df = df[df['is_win']]
loss_df = df[~df['is_win']]

print(f"{'Cohort':<22} | {'Count':<8} | {'Avg PnL (R)':<14} | {'Mean MAE (R)':<14} | {'Mean MFE (R)':<14}")
print("-" * 90)
print(f"{'Winning Trades':<22} | {len(win_df):<8} | {win_df['raw_r'].mean():<+13.2f}R | {win_df['mae_r'].mean():<13.2f}R | {win_df['mfe_r'].mean():<13.2f}R")
print(f"{'Losing Trades':<22} | {len(loss_df):<8} | {loss_df['raw_r'].mean():<+13.2f}R | {loss_df['mae_r'].mean():<13.2f}R | {loss_df['mfe_r'].mean():<13.2f}R")
print("-" * 90)

# B. MAE Distribution on Winners
mae_under_50 = (win_df['mae_r'] <= 0.50).sum()
mae_50_to_75 = ((win_df['mae_r'] > 0.50) & (win_df['mae_r'] <= 0.75)).sum()
mae_over_75  = (win_df['mae_r'] > 0.75).sum()

print("\n📊 Winner Heat Profile (How deep did winning trades pull back before running?):")
print(f"  • Shallow Heat (MAE <= 0.50R)   : {mae_under_50} / {len(win_df)} ({mae_under_50/len(win_df)*100:.1f}%)")
print(f"  • Moderate Heat (0.50R - 0.75R) : {mae_50_to_75} / {len(win_df)} ({mae_50_to_75/len(win_df)*100:.1f}%)")
print(f"  • Severe Heat (MAE > 0.75R)     : {mae_over_75} / {len(win_df)} ({mae_over_75/len(win_df)*100:.1f}%)")

# C. Ticker Attribution
print("\n📈 Ticker Attribution & Excursion Matrix:")
ticker_grp = df.groupby('symbol').agg(
    Trades=('raw_r', 'count'),
    WinRate=('is_win', lambda x: f"{x.mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    Avg_MAE=('mae_r', 'mean'),
    Avg_MFE=('mfe_r', 'mean')
).reset_index()
print(ticker_grp.to_string(index=False))

print("\n" + "=" * 90)
print(" 🎲 2. MONTE CARLO SIMULATION (10,000 RESHUFFLES @ 1.0X FLAT SIZING)")
print("=" * 90)

trade_returns = df['raw_r'].values
NUM_SIMS = 10000
N_TRADES = len(trade_returns)

max_drawdowns = []
max_losing_streaks = []
final_harvests = []

np.random.seed(42)

for _ in range(NUM_SIMS):
    # Reshuffle with replacement to test path dependency
    shuffled = np.random.choice(trade_returns, size=N_TRADES, replace=True)
    cum = np.cumsum(shuffled)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_drawdowns.append(np.max(dd))
    final_harvests.append(cum[-1])
    
    # Losing streak calculation
    is_loss = (shuffled <= 0).astype(int)
    streak = 0
    max_s = 0
    for val in is_loss:
        if val == 1:
            streak += 1
            max_s = max(max_s, streak)
        else:
            streak = 0
    max_losing_streaks.append(max_s)

max_dd_arr = np.array(max_drawdowns)
streak_arr = np.array(max_losing_streaks)
harvest_arr = np.array(final_harvests)

print(f"{'Metric':<32} | {'50th %ile (Median)':<20} | {'95th %ile (Risk Tail)':<20} | {'99th %ile (Worst Case)':<20}")
print("-" * 90)
print(f"{'Max Drawdown in R':<32} | {np.percentile(max_dd_arr, 50):<19.2f}R | {np.percentile(max_dd_arr, 95):<19.2f}R | {np.percentile(max_dd_arr, 99):<19.2f}R")
print(f"{'Max Consecutive Losing Streak':<32} | {int(np.percentile(streak_arr, 50)):<20} | {int(np.percentile(streak_arr, 95)):<20} | {int(np.percentile(streak_arr, 99)):<20}")
print(f"{'Simulated Total Harvest (R)':<32} | {np.percentile(harvest_arr, 50):<+19.2f}R | {np.percentile(harvest_arr, 5):<+19.2f}R | {np.percentile(harvest_arr, 1):<+19.2f}R")
print("-" * 90)
print(f"• Probability of Cumulative Net Profit > +20R : {(harvest_arr >= 20.0).sum() / NUM_SIMS * 100:.2f}%")
print(f"• Probability of Max Drawdown exceeding 8.0R  : {(max_dd_arr > 8.0).sum() / NUM_SIMS * 100:.2f}%")
print("=" * 90)
