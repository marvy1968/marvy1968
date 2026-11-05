#!/usr/bin/env python3
import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path

import run_f0_hardened_portfolio_v2 as canonical_engine
from run_hardened_challenger_framework import print_portfolio_metrics
from run_h4_filter_sweep import generate_filtered_signals as f0_signal_generator, get_pip_value

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
CANONICAL_DIR = Path("/home/moonrider1968/bots/oanda_data/h4")

PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

print("=" * 85)
print("PHASE 2: OVERLAP PARITY BRIDGE (CANONICAL 2023-2026 WINDOW)")
print("=" * 85)

# 1. Inspect date bounds of canonical dataset
canonical_dfs = {}
for pair in PAIRS:
    p = CANONICAL_DIR / f"{pair}_H4.csv"
    if not p.exists():
        # Fallback to alternate local name if standard path differs
        p = Path(f"/home/moonrider1968/bots/data_h4/{pair}_H4.csv")
    df = pd.read_csv(p)
    df["time"] = pd.to_datetime(df["time"] if "time" in df.columns else df["timestamp"], utc=True)
    canonical_dfs[pair] = df

min_canon_time = min(df["time"].min() for df in canonical_dfs.values())
max_canon_time = max(df["time"].max() for df in canonical_dfs.values())

print(f"Canonical Dataset Horizon: {min_canon_time} -> {max_canon_time}")

# 2. Load Extended Data
ext_dfs = {}
for pair in PAIRS:
    p = EXTENDED_DIR / f"{pair}_H4.csv"
    df = pd.read_csv(p)
    df["time"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("time").sort_index()
    ext_dfs[pair] = df

# 3. Generate Signals on Extended Data, but restrict entry evaluation to canonical bounds
print("\nExtracting F0 candidate events on extended data restricted to canonical window...")
extended_events = []

for pair in PAIRS:
    df = ext_dfs[pair].copy()
    pip_val = get_pip_value(pair)
    
    # Generate canonical F0 signals on the continuous series
    sig_df = f0_signal_generator(df.reset_index(), pair)
    if sig_df.empty:
        continue
        
    sig_df["timestamp"] = pd.to_datetime(sig_df["timestamp"], utc=True)
    
    # Restrict to identical date boundary as canonical control
    sig_filtered = sig_df[(sig_df["timestamp"] >= min_canon_time) & (sig_df["timestamp"] <= max_canon_time)]
    
    # Build trade events using canonical simulation bars
    for _, row in sig_filtered.iterrows():
        t_entry = row["timestamp"]
        if t_entry not in df.index:
            continue
        
        idx = df.index.get_loc(t_entry)
        if idx + 1 >= len(df):
            continue
            
        entry_bar = df.iloc[idx + 1]
        entry_time = df.index[idx + 1]
        entry_px = float(entry_bar["open"])
        side = row["side"]
        stop_dist_quote = row["stop_pips"] * pip_val
        
        if side == "BUY":
            stop_px = entry_px - stop_dist_quote
            target_px = float(row["target_px"])
        else:
            stop_px = entry_px + stop_dist_quote
            target_px = float(row["target_px"])
            
        # Simulate execution path
        bars_held = 0
        exit_px = None
        exit_time = None
        r_mult = 0.0
        
        for k in range(idx + 1, min(idx + 1 + 50, len(df))):
            b = df.iloc[k]
            bars_held += 1
            h = float(b["high"])
            l = float(b["low"])
            
            if side == "BUY":
                if l <= stop_px:
                    exit_px = stop_px
                    exit_time = df.index[k]
                    r_mult = -1.0
                    break
                elif h >= target_px:
                    exit_px = target_px
                    exit_time = df.index[k]
                    r_mult = (target_px - entry_px) / stop_dist_quote
                    break
            else:
                if h >= stop_px:
                    exit_px = stop_px
                    exit_time = df.index[k]
                    r_mult = -1.0
                    break
                elif l <= target_px:
                    exit_px = target_px
                    exit_time = df.index[k]
                    r_mult = (entry_px - target_px) / stop_dist_quote
                    break
                    
        if exit_px is None:
            last_b = df.iloc[min(idx + 50, len(df) - 1)]
            exit_px = float(last_b["close"])
            exit_time = df.index[min(idx + 50, len(df) - 1)]
            r_mult = (exit_px - entry_px)/stop_dist_quote if side == "BUY" else (entry_px - exit_px)/stop_dist_quote
            
        extended_events.append({
            "pair": pair,
            "side": side,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "stop_dist_quote": stop_dist_quote,
            "bars_held": bars_held,
            "r_mult": r_mult,
            "strategy": "F0"
        })

df_events = pd.DataFrame(extended_events).sort_values("entry_time").reset_index(drop=True)

print(f"Total Extracted Trades on Extended Source (Common Window): {len(df_events)}")
w = df_events[df_events["r_mult"] > 0]["r_mult"].sum()
l = abs(df_events[df_events["r_mult"] <= 0]["r_mult"].sum())
pf = w / l if l > 0 else float("inf")
mean_r = df_events["r_mult"].mean()

print(f"Mean Realized R: {mean_r:+.3f}R (Canonical Expected: +0.101R)")
print(f"Profit Factor:   {pf:.2f} (Canonical Expected: 1.16)")

if len(df_events) == 628 and abs(mean_r - 0.101) < 0.005:
    print("\n>>> OVERLAP_PARITY_BRIDGE = PASS <<<")
    print("Zero indicator drift or candle alignment divergence detected.")
else:
    print(f"\n>>> OVERLAP_PARITY_BRIDGE = DIVERGENCE DETECTED <<<")
    print(f"Candidate count delta: {len(df_events) - 628}")
