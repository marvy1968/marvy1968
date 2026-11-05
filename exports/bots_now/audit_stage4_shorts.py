import numpy as np
import pandas as pd
from backtest_multi_regime_v3_3 import master_trades

if not master_trades:
    print("❌ No trades found in backtest_multi_regime_v3_3 memory. Re-running required.")
    exit()

all_df = pd.concat(master_trades)
s4_shorts = all_df[(all_df['regime'] == '4_BEAR_STAGE4') & (all_df['side'] == 'SHORT')]

if s4_shorts.empty:
    print("❌ No Stage-4 shorts extracted.")
    exit()

print("=" * 100)
print(f" 🔬 FORENSIC AUDIT: PROVEN STAGE-4 SHORT ENGINE (N = {len(s4_shorts)} Trades)")
print("=" * 100)

r = s4_shorts['raw_r'].values
wins = r[r > 0]
losses = r[r <= 0]
cum = np.cumsum(r)
peak = np.maximum.accumulate(cum)
max_dd = np.max(peak - cum)

print(f"• Win Rate                  : {(len(wins)/len(r))*100:.1f}%")
print(f"• Total Harvest (R)         : {r.sum():+.2f}R")
print(f"• Profit Factor             : {abs(wins.sum()/(losses.sum()+1e-9)):.2f}")
print(f"• Expectancy per Trade      : {r.mean():+.2f}R")
print(f"• Average Winner            : {wins.mean():+.2f}R")
print(f"• Average Loser             : {losses.mean():+.2f}R")
print(f"• Realized Max Drawdown     : {max_dd:.2f}R")
print(f"• Harvest / MaxDD Ratio     : {r.sum()/max_dd:.2f}")
print(f"• Mean Adverse Excursion    : {s4_shorts['mae_r'].mean():.2f}R")
print(f"• Mean Favorable Excursion   : {s4_shorts['mfe_r'].mean():.2f}R")
print("-" * 100)

print("\n📊 Sector Attribution Breakdown:")
sec_grp = s4_shorts.groupby('sector').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(sec_grp.to_string(index=False))

print("\n📈 Ticker Attribution Breakdown:")
tick_grp = s4_shorts.groupby('symbol').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(tick_grp.to_string(index=False))
print("=" * 100)
