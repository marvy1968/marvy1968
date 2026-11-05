#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

import run_hardened_challenger_framework as framework
from run_hardened_challenger_framework import build_events_from_signals
from run_h4_filter_sweep import get_pip_value

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

print("=" * 90)
print("STANDALONE AUDIT: 5TH CANDLE MEAN-REVERSION REVERSAL (20 SMA)")
print("CANONICAL 9 PAIRS | 2016 - 2026 (10.67 YEARS) | FULL FRICTION")
print("=" * 90)

# 1. Load extended data and patch framework globals
ext_dfs = {}
for pair in PAIRS:
    p = EXTENDED_DIR / f"{pair}_H4.csv"
    df = pd.read_csv(p)
    tcol = "timestamp" if "timestamp" in df.columns else "time"
    df["time"] = pd.to_datetime(df[tcol], utc=True)
    df = df.sort_values("time").reset_index(drop=True).set_index("time")
    ext_dfs[pair] = df

framework.dfs = ext_dfs
all_times_sorted = sorted(list(set().union(*(df.index for df in ext_dfs.values()))))
framework.all_times = all_times_sorted
framework.time_to_idx = {pair: {t: i for i, t in enumerate(ext_dfs[pair].index)} for pair in PAIRS}

# 2. Indicator Calculation
def calc_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

# 3. Signal Generation
def generate_5candle_reversal_signals(df_input, symbol):
    df = df_input.copy()
    pip_val = get_pip_value(symbol)
    
    sma20 = df["close"].rolling(20).mean()
    atr14 = calc_atr(df, 14)
    
    signals = []
    
    # Track consecutive closes above/below mean
    below_count = 0
    above_count = 0
    
    for i in range(25, len(df)):
        c = df["close"].iloc[i]
        mean_px = sma20.iloc[i]
        atr_val = atr14.iloc[i]
        t = df.index[i]
        
        if pd.isna(mean_px) or pd.isna(atr_val):
            continue
            
        if c < mean_px:
            below_count += 1
            above_count = 0
        elif c > mean_px:
            above_count += 1
            below_count = 0
        else:
            below_count = 0
            above_count = 0
            
        stop_pips = (1.5 * atr_val) / pip_val
        
        # BUY trigger on exactly the 5th candle below the mean
        if below_count == 5:
            target_px = mean_px
            if target_px > c + (10 * pip_val):  # Ensure minimal distance to target
                signals.append({
                    "timestamp": t,
                    "side": "BUY",
                    "stop_pips": max(20.0, stop_pips),
                    "target_px": target_px,
                    "pass_filter": True
                })
        # SELL trigger on exactly the 5th candle above the mean
        elif above_count == 5:
            target_px = mean_px
            if target_px < c - (10 * pip_val):
                signals.append({
                    "timestamp": t,
                    "side": "SELL",
                    "stop_pips": max(20.0, stop_pips),
                    "target_px": target_px,
                    "pass_filter": True
                })
                
    return pd.DataFrame(signals)

# 4. Extract Events and Evaluate Path
print("Extracting 5-candle reversal candidates across all 9 pairs...")
events_rev = build_events_from_signals(generate_5candle_reversal_signals, "REV_5C")
print(f"Total Candidate Signals Extracted: {len(events_rev)}")

if events_rev.empty:
    print("No events met filter criteria.")
    sys.exit(0)

events_rev["entry_time"] = pd.to_datetime(events_rev["entry_time"], utc=True)
events_rev["year"] = events_rev["entry_time"].dt.year

# 5. Metrics
w = events_rev[events_rev["r_mult"] > 0]["r_mult"].sum()
l = abs(events_rev[events_rev["r_mult"] <= 0]["r_mult"].sum())
pf = w / l if l > 0 else float("inf")
mean_r = events_rev["r_mult"].mean()
win_rate = (events_rev["r_mult"] > 0).mean() * 100.0

print("\n" + "=" * 90)
print("5-CANDLE REVERSAL BENCHMARK SUMMARY (10.67 YEARS)")
print("=" * 90)
print(f"Total Trades:        {len(events_rev)}")
print(f"Win Rate:            {win_rate:5.1f}%")
print(f"Profit Factor:       {pf:5.2f}")
print(f"Net Expectancy (R):  {mean_r:+6.3f}R")
print(f"Total Realized R:    {events_rev['r_mult'].sum():+6.2f}R")

print("\nYear-by-Year Expectancy Breakdown:")
print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Mean R':<10} | {'PF':<6} | {'Net R':<10}")
print("-" * 60)
for yr, grp in events_rev.groupby("year"):
    w_yr = grp[grp["r_mult"] > 0]["r_mult"].sum()
    l_yr = abs(grp[grp["r_mult"] <= 0]["r_mult"].sum())
    pf_yr = w_yr / l_yr if l_yr > 0 else float("inf")
    wr_yr = (grp["r_mult"] > 0).mean() * 100.0
    mr_yr = grp["r_mult"].mean()
    tot_r = grp["r_mult"].sum()
    print(f"{yr:<6} | {len(grp):<8} | {wr_yr:>6.1f}%    | {mr_yr:>+6.3f}R   | {pf_yr:4.2f} | {tot_r:>+7.2f}R")
