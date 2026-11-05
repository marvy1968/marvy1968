#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

import run_hardened_challenger_framework as framework
from run_hardened_challenger_framework import build_events_from_signals, print_portfolio_metrics
from run_h4_filter_sweep import get_pip_value

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

print("=" * 90)
print("STANDALONE SLEEVE AUDIT: H4 MEAN-REVERSION (BB + RSI + ADX < 20)")
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

# 2. Define Technical Indicators
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))

def calc_adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100.0 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx, atr

# 3. Generate Standalone Mean-Reversion Signals
def generate_mean_reversion_signals(df_input, symbol):
    df = df_input.copy()
    pip_val = get_pip_value(symbol)
    
    # Bollinger Bands (20, 2.5)
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    upper_bb = sma20 + (2.5 * std20)
    lower_bb = sma20 - (2.5 * std20)
    
    # RSI (14) & ADX (14)
    rsi14 = calc_rsi(df["close"], 14)
    adx14, atr14 = calc_adx(df, 14)
    
    signals = []
    for i in range(30, len(df)):
        c = df["close"].iloc[i]
        adx_val = adx14.iloc[i]
        rsi_val = rsi14.iloc[i]
        mean_px = sma20.iloc[i]
        atr_val = atr14.iloc[i]
        t = df.index[i]
        
        # Regime Gate: Low-trend environment only
        if adx_val >= 20.0 or pd.isna(adx_val):
            continue
            
        stop_pips = (2.0 * atr_val) / pip_val
        
        # Oversold Long Condition
        if c <= lower_bb.iloc[i] and rsi_val <= 30.0:
            target_px = mean_px
            if target_px > c:
                signals.append({
                    "timestamp": t,
                    "side": "BUY",
                    "stop_pips": max(25.0, stop_pips),
                    "target_px": target_px,
                    "pass_filter": True
                })
        # Overbought Short Condition
        elif c >= upper_bb.iloc[i] and rsi_val >= 70.0:
            target_px = mean_px
            if target_px < c:
                signals.append({
                    "timestamp": t,
                    "side": "SELL",
                    "stop_pips": max(25.0, stop_pips),
                    "target_px": target_px,
                    "pass_filter": True
                })
                
    return pd.DataFrame(signals)

# 4. Extract Events and Evaluate Path
print("Extracting candidates across all 9 pairs...")
events_mr = build_events_from_signals(generate_mean_reversion_signals, "MEAN_REV")
print(f"Total Candidate Signals Extracted: {len(events_mr)}")

if events_mr.empty:
    print("No events met filter criteria.")
    sys.exit(0)

events_mr["entry_time"] = pd.to_datetime(events_mr["entry_time"], utc=True)
events_mr["year"] = events_mr["entry_time"].dt.year

# 5. Standalone Performance Metrics
w = events_mr[events_mr["r_mult"] > 0]["r_mult"].sum()
l = abs(events_mr[events_mr["r_mult"] <= 0]["r_mult"].sum())
pf = w / l if l > 0 else float("inf")
mean_r = events_mr["r_mult"].mean()
win_rate = (events_mr["r_mult"] > 0).mean() * 100.0

print("\n" + "=" * 90)
print("STANDALONE SLEEVE BENCHMARK SUMMARY")
print("=" * 90)
print(f"Total Trades:        {len(events_mr)}")
print(f"Win Rate:            {win_rate:5.1f}%")
print(f"Profit Factor:       {pf:5.2f}")
print(f"Net Expectancy (R):  {mean_r:+6.3f}R")
print(f"Total Realized R:    {events_mr['r_mult'].sum():+6.2f}R")

print("\nYear-by-Year Expectancy Breakdown:")
print(f"{'Year':<6} | {'Trades':<8} | {'Win Rate':<10} | {'Mean R':<10} | {'PF':<6} | {'Net R':<10}")
print("-" * 60)
for yr, grp in events_mr.groupby("year"):
    w_yr = grp[grp["r_mult"] > 0]["r_mult"].sum()
    l_yr = abs(grp[grp["r_mult"] <= 0]["r_mult"].sum())
    pf_yr = w_yr / l_yr if l_yr > 0 else float("inf")
    wr_yr = (grp["r_mult"] > 0).mean() * 100.0
    mr_yr = grp["r_mult"].mean()
    tot_r = grp["r_mult"].sum()
    print(f"{yr:<6} | {len(grp):<8} | {wr_yr:>6.1f}%    | {mr_yr:>+6.3f}R   | {pf_yr:4.2f} | {tot_r:>+7.2f}R")
