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

print("=" * 95)
print("EMPIRICAL EXHAUSTION SWEEP: N CONSECUTIVE CLOSES AWAY FROM 20 SMA")
print("CANONICAL 9 PAIRS | 2016 - 2026 (16,655 BARS) | FULL FRICTION")
print("=" * 95)

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

# 3. Parametric Streak Signal Generator
def make_streak_generator(n_streak):
    def generator(df_input, symbol):
        df = df_input.copy()
        pip_val = get_pip_value(symbol)
        sma20 = df["close"].rolling(20).mean()
        atr14 = calc_atr(df, 14)
        
        signals = []
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
            
            # BUY on Nth candle below mean
            if below_count == n_streak:
                target_px = mean_px
                if target_px > c + (10 * pip_val):
                    signals.append({
                        "timestamp": t,
                        "side": "BUY",
                        "stop_pips": max(20.0, stop_pips),
                        "target_px": target_px,
                        "pass_filter": True
                    })
            # SELL on Nth candle above mean
            elif above_count == n_streak:
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
    return generator

# 4. Sweep N from 2 to 10
results = []
print(f"{'Streak N':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Mean Expectancy':<18} | {'Profit Factor':<15} | {'Total Realized R':<18}")
print("-" * 90)

for n in range(2, 11):
    gen_func = make_streak_generator(n)
    events = build_events_from_signals(gen_func, f"REV_{n}C")
    
    if events.empty:
        print(f"Candle {n:<3} | No events")
        continue
        
    w = events[events["r_mult"] > 0]["r_mult"].sum()
    l = abs(events[events["r_mult"] <= 0]["r_mult"].sum())
    pf = w / l if l > 0 else float("inf")
    mean_r = events["r_mult"].mean()
    win_rate = (events["r_mult"] > 0).mean() * 100.0
    tot_r = events["r_mult"].sum()
    
    results.append({
        "N": n,
        "Trades": len(events),
        "WinRate": win_rate,
        "MeanR": mean_r,
        "PF": pf,
        "TotalR": tot_r
    })
    
    print(f"Candle {n:<3} | {len(events):<8} | {win_rate:>6.1f}%    | {mean_r:>+7.3f}R{' '*9} | {pf:>6.2f}{' '*8} | {tot_r:>+9.2f}R")

df_res = pd.DataFrame(results)
print("\n" + "=" * 95)
best_pf = df_res.loc[df_res['PF'].idxmax()]
print(f"Mathematical Peak: Candle N = {int(best_pf['N'])} (PF: {best_pf['PF']:.2f}, Mean R: {best_pf['MeanR']:+.3f}R, Trades: {int(best_pf['Trades'])})")
print("=" * 95)
