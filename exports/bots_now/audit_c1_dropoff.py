#!/usr/bin/env python3
import sys
import pandas as pd
import numpy as np
from pathlib import Path

import run_f0_hardened_portfolio_v2 as engine
import run_hardened_challenger_framework as framework
from run_hardened_challenger_framework import build_events_from_signals
from run_h4_filter_sweep import generate_filtered_signals as f0_signals
from test_gated_f0_donchian import generate_gated_donchian_signals
import test_reserved_slot_2plus1 as mod_c1

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

print("=" * 90)
print("AUDITING C1 PIPELINE STAGE-BY-STAGE (2016 - 2026)")
print("=" * 90)

# 1. Load extended data
ext_dfs = {}
for pair in PAIRS:
    p = EXTENDED_DIR / f"{pair}_H4.csv"
    df = pd.read_csv(p)
    tcol = "timestamp" if "timestamp" in df.columns else "time"
    df["time"] = pd.to_datetime(df[tcol], utc=True)
    df = df.sort_values("time").reset_index(drop=True).set_index("time")
    ext_dfs[pair] = df

engine.dfs = ext_dfs
framework.dfs = ext_dfs
mod_c1.dfs = ext_dfs

all_times_sorted = sorted(list(set().union(*(df.index for df in ext_dfs.values()))))
engine.all_times = all_times_sorted
framework.all_times = all_times_sorted
mod_c1.all_times = all_times_sorted

time_to_idx_dict = {pair: {t: i for i, t in enumerate(ext_dfs[pair].index)} for pair in PAIRS}
engine.time_to_idx = time_to_idx_dict
framework.time_to_idx = time_to_idx_dict
mod_c1.time_to_idx = time_to_idx_dict

# 2. Extract Raw Events
ev_f0 = build_events_from_signals(f0_signals, "F0")
ev_f0["strategy"] = "F0"
ev_f0["priority"] = 1

ev_don = build_events_from_signals(generate_gated_donchian_signals, "DONCHIAN_GATED")
ev_don["strategy"] = "DONCHIAN_GATED"
ev_don["priority"] = 2

ev_f0["year"] = ev_f0["entry_time"].dt.year
ev_don["year"] = ev_don["entry_time"].dt.year

# 3. Merged Candidates
combined = pd.concat([ev_f0, ev_don], ignore_index=True).sort_values(
    by=["entry_time", "priority", "pair"]
).reset_index(drop=True)
combined["year"] = combined["entry_time"].dt.year

print("\nSTAGE 1: GENERATED CANDIDATES PER YEAR")
stage1 = pd.DataFrame({
    "F0_Candidates": ev_f0.groupby("year").size(),
    "Donchian_Candidates": ev_don.groupby("year").size(),
    "Merged_Total": combined.groupby("year").size()
}).fillna(0).astype(int)
print(stage1.to_string())

# 4. Check simulator internals directly
print("\nSTAGE 2: SIMULATOR TIMELINE INSPECTION")
print(f"mod_c1.all_times range: {mod_c1.all_times[0]} -> {mod_c1.all_times[-1]} (Total: {len(mod_c1.all_times)})")
print(f"combined events range:  {combined['entry_time'].min()} -> {combined['entry_time'].max()} (Total: {len(combined)})")

# Run reserved simulator and capture trade log
nav_c1, trades_c1 = mod_c1.simulate_h4_portfolio_reserved(combined, max_positions=3, use_firewall=True)
trades_c1["entry_time"] = pd.to_datetime(trades_c1["entry_time"], utc=True)
trades_c1["year"] = trades_c1["entry_time"].dt.year

print("\nSTAGE 3: EXECUTED TRADES PER YEAR (C1)")
c1_exec = trades_c1.groupby(["year", "strategy"]).size().unstack().fillna(0).astype(int)
c1_exec["Total_Executed"] = c1_exec.sum(axis=1)
print(c1_exec.to_string())

# Audit first candidate timestamps vs first executed trade timestamp
print("\nSTAGE 4: FIRST TIMESTAMPS")
print(f"First 3 Merged Candidates:\n{combined[['pair', 'strategy', 'entry_time']].head(3)}")
print(f"\nFirst 3 Executed Trades:\n{trades_c1[['pair', 'strategy', 'entry_time']].head(3)}")
