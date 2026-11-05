#!/usr/bin/env python3
import pandas as pd
from attribution_book_a import load_data, run_attribution_sim

dfs = load_data()
tdf = run_attribution_sim(dfs)

print("=" * 72)
print(" STOCK PORTFOLIO OPTIMIZATION & PRUNING AUDIT (BOOK A)")
print("=" * 72)

# 1. Full Universe Baseline
wins_full = tdf[tdf['net_pnl'] > 0]
wr_full = (len(wins_full) / len(tdf)) * 100.0
pf_full = wins_full['net_pnl'].sum() / abs(tdf[tdf['net_pnl'] <= 0]['net_pnl'].sum())

print(f"BASELINE (ALL 19 SYMBOLS):")
print(f"  Trades: {len(tdf)} | Win Rate: {wr_full:.1f}% | Total Net R: {tdf['r_multiple'].sum():+.2f}R")
print(f"  Total P&L: ${tdf['net_pnl'].sum():,.2f} | Profit Factor: {pf_full:.2f}")

# 2. Pruned Universe (Excluding persistent negative expectancy drag)
toxic = ["AAPL", "AVGO", "COP", "PG"]
pruned = tdf[~tdf["symbol"].isin(toxic)]
wins_pruned = pruned[pruned['net_pnl'] > 0]
wr_pruned = (len(wins_pruned) / len(pruned)) * 100.0
pf_pruned = wins_pruned['net_pnl'].sum() / abs(pruned[pruned['net_pnl'] <= 0]['net_pnl'].sum())

print("\nPRUNED UNIVERSE (REMOVING AAPL, AVGO, COP, PG):")
print(f"  Trades: {len(pruned)} | Win Rate: {wr_pruned:.1f}% | Total Net R: {pruned['r_multiple'].sum():+.2f}R")
print(f"  Total P&L: ${pruned['net_pnl'].sum():,.2f} | Profit Factor: {pf_pruned:.2f}")
print(f"  Net P&L Improvement: +${pruned['net_pnl'].sum() - tdf['net_pnl'].sum():,.2f} (+{((pruned['net_pnl'].sum() / tdf['net_pnl'].sum()) - 1.0) * 100:.1f}%)")
