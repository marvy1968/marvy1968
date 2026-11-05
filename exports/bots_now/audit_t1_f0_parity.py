#!/usr/bin/env python3
"""
Diagnostic Parity Audit: T1 Target Baseline vs F0 Filter Baseline
Identifies and verifies all dropped events between Index 105 and Index 205.
"""
import pandas as pd
from pathlib import Path
from run_h4_target_sweep import load_local_dataset, generate_h4_signals, run_target_simulation
from run_h4_filter_sweep import generate_filtered_signals, run_simulation

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

df_uj_h1 = load_local_dataset("USD_JPY", "H1")
pit_usdjpy = df_uj_h1["close"].shift(1)

print("=" * 95)
print(" 🔍 PARITY AUDIT: T1 BASELINE (Start Idx 105) vs F0 BASELINE (Start Idx 205)")
print("=" * 95)
print(f"{'Symbol':<10} | {'T1 Events':<10} | {'F0 Events':<10} | {'Delta':<8} | {'Dropped Event Timestamps'}")
print("-" * 95)

total_t1, total_f0 = 0, 0
dropped_records = []

for sym in PAIRS:
    df_h4 = load_local_dataset(sym, "H4")
    sig_t1 = generate_h4_signals(df_h4, sym)
    sig_f0 = generate_filtered_signals(df_h4, sym)
    
    t1_trades, _ = run_target_simulation(sym, df_h4, sig_t1, "T1", pit_usdjpy)
    f0_trades, _ = run_simulation(sym, df_h4, sig_f0, "pass_f0", pit_usdjpy)
    
    total_t1 += len(t1_trades)
    total_f0 += len(f0_trades)
    delta = len(t1_trades) - len(f0_trades)
    
    t1_times = set(t.entry_time for t in t1_trades)
    f0_times = set(t.entry_time for t in f0_trades)
    dropped = sorted(list(t1_times - f0_times))
    
    ts_str = ", ".join(str(ts.date()) for ts in dropped) if dropped else "None (Exact Match)"
    print(f"{sym:<10} | {len(t1_trades):<10d} | {len(f0_trades):<10d} | {delta:<8d} | {ts_str}")

print("=" * 95)
print(f" TOTALS     | {total_t1:<10d} | {total_f0:<10d} | {total_t1 - total_f0:<8d}")
print("=" * 95)
