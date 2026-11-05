#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from run_hardened_challenger_framework import (
    build_events_from_signals,
    simulate_h4_portfolio
)
from run_h4_filter_sweep import generate_filtered_signals as f0_signals
from test_challenger_donchian import generate_donchian_signals
from test_challenger_squeeze import generate_squeeze_signals

print("=" * 80)
print("EXTRACTING CANONICAL RUNS FOR CROSS-STRATEGY ENSEMBLE ANALYSIS")
print("=" * 80)

# Build events and simulate Portfolio 3 for all 3 families
print("Running F0...")
nav_f0, trades_f0 = simulate_h4_portfolio(build_events_from_signals(f0_signals), 3, True)
print("Running Donchian...")
nav_don, trades_don = simulate_h4_portfolio(build_events_from_signals(generate_donchian_signals), 3, True)
print("Running Squeeze...")
nav_sq, trades_sq = simulate_h4_portfolio(build_events_from_signals(generate_squeeze_signals), 3, True)

# Prepare Monthly PnL Series
def get_monthly_pnl(trades_df):
    t = trades_df.copy()
    t["month"] = t["exit_time"].dt.to_period("M")
    return t.groupby("month")["pnl_usd"].sum()

m_f0 = get_monthly_pnl(trades_f0).rename("F0_Reversion")
m_don = get_monthly_pnl(trades_don).rename("Donchian_Breakout")
m_sq = get_monthly_pnl(trades_sq).rename("Squeeze_Breakout")

df_monthly = pd.concat([m_f0, m_don, m_sq], axis=1).fillna(0.0)

print("\n" + "=" * 80)
print("MONTHLY PNL CORRELATION MATRIX")
print("=" * 80)
corr_matrix = df_monthly.corr()
print(corr_matrix.round(3).to_string())

print("\n" + "=" * 80)
print("YEARLY PNL BREAKDOWN ($)")
print("=" * 80)
y_f0 = trades_f0.groupby("year")["pnl_usd"].sum().rename("F0")
y_don = trades_don.groupby("year")["pnl_usd"].sum().rename("Donchian")
y_sq = trades_sq.groupby("year")["pnl_usd"].sum().rename("Squeeze")
df_yearly = pd.concat([y_f0, y_don, y_sq], axis=1).fillna(0.0)
df_yearly["F0 + Donchian (50/50 Dollar)"] = (df_yearly["F0"] + df_yearly["Donchian"]) / 2.0
print(df_yearly.round(2).to_string())

print("\n" + "=" * 80)
print("DRAWDOWN OVERLAP (MONTHS WHERE BOTH WERE NEGATIVE)")
print("=" * 80)
both_neg = (df_monthly["F0_Reversion"] < 0) & (df_monthly["Donchian_Breakout"] < 0)
f0_neg_don_pos = (df_monthly["F0_Reversion"] < 0) & (df_monthly["Donchian_Breakout"] > 0)
don_neg_f0_pos = (df_monthly["Donchian_Breakout"] < 0) & (df_monthly["F0_Reversion"] > 0)

total_months = len(df_monthly)
print(f"Total Active Months:              {total_months}")
print(f"Both Losing Together:             {both_neg.sum()} months ({both_neg.sum()/total_months*100:.1f}%)")
print(f"Donchian Offsets F0 Loss:         {f0_neg_don_pos.sum()} months ({f0_neg_don_pos.sum()/total_months*100:.1f}%)")
print(f"F0 Offsets Donchian Loss:         {don_neg_f0_pos.sum()} months ({don_neg_f0_pos.sum()/total_months*100:.1f}%)")
print(f"Total Complementary Hedged Months:{ (f0_neg_don_pos.sum() + don_neg_f0_pos.sum()) / total_months * 100:.1f}%")
