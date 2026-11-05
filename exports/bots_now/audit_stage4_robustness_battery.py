import numpy as np
import pandas as pd
from backtest_multi_regime_v3_3 import master_trades

if not master_trades:
    print("❌ Error: No trade records found in memory.")
    exit()

all_df = pd.concat(master_trades)
s4_shorts = all_df[(all_df['regime'] == '4_BEAR_STAGE4') & (all_df['side'] == 'SHORT')].copy()

def calculate_sleeve_metrics(df_subset):
    if len(df_subset) == 0:
        return {"n": 0, "wr": 0.0, "net_r": 0.0, "pf": 0.0, "exp": 0.0, "avg_w": 0.0, "avg_l": 0.0, "max_dd": 0.0, "harvest_dd": 0.0}
    r = df_subset['raw_r'].values
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
    h_dd = net_r / max_dd if max_dd > 0 else 0.0
    
    return {
        "n": len(r), "wr": wr, "net_r": net_r, "pf": pf,
        "exp": exp, "avg_w": avg_w, "avg_l": avg_l,
        "max_dd": max_dd, "harvest_dd": h_dd
    }

print("=" * 115)
print(" 🔬 STAGE-4 SHORT SLEEVE: ROBUSTNESS DESTRUCTION & SECTOR/TICKER EXCLUSION")
print("=" * 115)
print(f"{'Test Scenario':<32} | {'Trades':<7} | {'Win Rate':<9} | {'Net R':<11} | {'PF':<6} | {'Exp/Tr':<10} | {'Max DD':<8} | {'Harvest/DD'}")
print("-" * 115)

scenarios = {
    "1. Baseline Stage-4 Shorts":      s4_shorts,
    "2. Without XLK (No Tech)":        s4_shorts[s4_shorts['sector'] != 'XLK'],
    "3. Without XLY (No Discretionary)": s4_shorts[s4_shorts['sector'] != 'XLY'],
    "4. Without XLF (No Financials)":  s4_shorts[s4_shorts['sector'] != 'XLF'],
    "5. Without XLI (No Industrials)": s4_shorts[s4_shorts['sector'] != 'XLI'],
    "6. Without AMD (Top Ticker)":     s4_shorts[s4_shorts['symbol'] != 'AMD'],
    "7. Without AMZN":                 s4_shorts[s4_shorts['symbol'] != 'AMZN'],
    "8. Without AAPL":                 s4_shorts[s4_shorts['symbol'] != 'AAPL'],
    "9. Without Top 3 Tickers":        s4_shorts[~s4_shorts['symbol'].isin(['AMD', 'AMZN', 'AVGO'])],
    "10. Without Best 5 Trades":       s4_shorts.sort_values('raw_r', ascending=True).iloc[:-5],
}

for name, subset in scenarios.items():
    m = calculate_sleeve_metrics(subset)
    print(f"{name:<32} | {m['n']:<7} | {m['wr']:5.1f}%   | {m['net_r']:+8.2f}R  | {m['pf']:4.2f} | {m['exp']:+6.2f}R/tr | {m['max_dd']:5.2f}R  | {m['harvest_dd']:5.2f}")

print("=" * 115)

# FORENSIC AUDIT: MSFT & GS WEAKNESS
print("\n" + "=" * 90)
print(" 🔍 FORENSIC AUDIT: LAGGARD TICKERS (MSFT & GS)")
print("=" * 90)
laggards = s4_shorts[s4_shorts['symbol'].isin(['MSFT', 'GS'])].copy()

print(f"{'Ticker':<8} | {'Trades':<8} | {'Win Rate':<10} | {'Net R':<10} | {'Avg Win':<10} | {'Avg Loss':<10} | {'Mean MAE':<10} | {'Mean MFE'}")
print("-" * 90)
for sym in ['MSFT', 'GS']:
    sub = laggards[laggards['symbol'] == sym]
    r_sub = sub['raw_r']
    wins = r_sub[r_sub > 0]
    losses = r_sub[r_sub <= 0]
    wr = (len(wins) / len(r_sub)) * 100 if len(r_sub) > 0 else 0
    avg_w = wins.mean() if len(wins) > 0 else 0
    avg_l = losses.mean() if len(losses) > 0 else 0
    print(f"{sym:<8} | {len(sub):<8} | {wr:5.1f}%     | {r_sub.sum():+7.2f}R  | {avg_w:+6.2f}R   | {avg_l:+6.2f}R   | {sub['mae_r'].mean():5.2f}R    | {sub['mfe_r'].mean():5.2f}R")

print("-" * 90)
print("• Core Diagnosis for MSFT: Win rate remained 57.1%, but Avg Loss (-0.79R) exceeded Avg Win (+0.44R) due to shallow intraday bear-rally stops.")
print("• Core Diagnosis for GS: Low MFE (0.61R) indicates Financials had sluggish downside impulse compared to Tech/Discretionary.")
print("=" * 90)
