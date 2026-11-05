#!/usr/bin/env python3
import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path

import run_f0_hardened_portfolio_v2 as canonical_engine
from run_hardened_challenger_framework import build_events_from_signals, print_portfolio_metrics
from run_h4_filter_sweep import generate_filtered_signals as f0_signal_generator

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")

PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

print("=" * 85)
print("PHASE 2: OVERLAP PARITY BRIDGE (CANONICAL 2023-2026 WINDOW)")
print("=" * 85)

# Inspect canonical date bounds
canon_start = min(canonical_engine.dfs[p].index.min() for p in PAIRS)
canon_end = max(canonical_engine.dfs[p].index.max() for p in PAIRS)
print(f"Canonical Engine Window: {canon_start} -> {canon_end}")

# Load extended data and slice strictly to canonical window
sliced_dfs = {}
for pair in PAIRS:
    p = EXTENDED_DIR / f"{pair}_H4.csv"
    df = pd.read_csv(p)
    # Match canonical column names exactly
    time_col = "timestamp" if "timestamp" in df.columns else "time"
    df["time"] = pd.to_datetime(df[time_col], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    
    # Slice to canonical boundary
    df_sliced = df[(df["time"] >= canon_start) & (df["time"] <= canon_end)].copy()
    df_sliced = df_sliced.set_index("time")
    sliced_dfs[pair] = df_sliced

# Re-point engine data structures to the sliced extended data
canonical_engine.dfs = sliced_dfs
all_times_set = set()
for df in sliced_dfs.values():
    all_times_set.update(df.index)
canonical_engine.all_times = sorted(list(all_times_set))
canonical_engine.time_to_idx = {
    pair: {t: i for i, t in enumerate(sliced_dfs[pair].index)}
    for pair in PAIRS
}

print("Running canonical event extraction on sliced extended data...")
events = build_events_from_signals(f0_signal_generator, "F0")

print(f"\nCandidates Extracted: {len(events)} (Expected: 628)")
w = events[events["r_mult"] > 0]["r_mult"].sum()
l = abs(events[events["r_mult"] <= 0]["r_mult"].sum())
pf = w / l if l > 0 else float("inf")
mean_r = events["r_mult"].mean()
print(f"Net Mean R:          {mean_r:+.3f}R (Expected: +0.101R)")
print(f"Profit Factor:       {pf:.2f} (Expected: 1.16)")

# Execute Portfolio 3 simulation
nav_p3, trades_p3 = canonical_engine.simulate_h4_portfolio(events, max_positions=3, use_firewall=True)
print_portfolio_metrics("SLICED EXTENDED DATA - PORTFOLIO 3 GOVERNED", nav_p3, trades_p3)

# Parity Assertion
parity_pass = (
    len(events) == 628 and 
    abs(mean_r - 0.101) < 0.005 and 
    len(trades_p3) == 278
)

if parity_pass:
    print("\n>>> OVERLAP_PARITY_BRIDGE = PASS <<<")
    print("Extended dataset perfectly matches canonical 2023-2026 ground truth.")
else:
    print("\n>>> OVERLAP_PARITY_BRIDGE = DIVERGENCE DETECTED <<<")
    print(f"Delta: Events={len(events)-628}, Portfolio3_Trades={len(trades_p3)-278}")
