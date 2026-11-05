#!/usr/bin/env python3
"""
MicroBeast OANDA M1 Fixed-R Backtest Runner
- Risk: 0.50% equity per trade
- Target: +1.50R
- Stop: -1.00R
- Max Duration: 30 completed M1 bars
- First Event Wins (Stop-First -> Target -> 30M Close)
- Trade-by-trade equity compounding with full spread & slippage friction
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import oanda_m1_fixed_r_harness as harness

DATA_SEARCH_DIRS = [
    Path("/home/moonrider1968/bots/oanda_active/data"),
    Path("/home/moonrider1968/bots/oanda_active"),
    Path("/home/moonrider1968/bots/data"),
    Path("/home/moonrider1968/bots/stock_data"),
    Path("/home/moonrider1968/bots"),
]

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

def find_m1_file(symbol: str) -> Optional[Path]:
    search_dirs = [
        Path("/home/moonrider1968/bots/oanda_data/m1"),
        Path("/home/moonrider1968/bots/oanda_data"),
        Path("/home/moonrider1968/bots/data/m1"),
        Path("/home/moonrider1968/bots/data"),
        Path("/home/moonrider1968/bots"),
    ]
    patterns = [
        f"{symbol}_M1.csv",
        f"{symbol}_m1.csv",
        f"{symbol.lower()}_m1.csv",
        f"{symbol.upper()}_M1.csv"
    ]
    for d in search_dirs:
        if d.exists():
            for p in patterns:
                target = d / p
                if target.exists():
                    return target
    return None

def generate_microbeast_m1_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Extracts MicroBeast momentum-breakout & pullback entry signals on M1 bars.
    Calculates dynamic stop distance using 14-period ATR (minimum floor 8 pips).
    """
    df = df.copy()
    pip_val = harness.get_pip_value(symbol, df["close"].iloc[0])
    
    # Core indicators
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    
    tr = np.maximum(df["high"] - df["low"], np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))))
    df["atr14"] = tr.rolling(14).mean()
    df["atr_pips"] = df["atr14"] / pip_val
    df["vol_sma20"] = df["volume"].rolling(20).mean() if "volume" in df.columns else 1.0
    
    signals = []
    
    for idx in range(200, len(df) - 35):
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]
        timestamp = df.index[idx]
        
        atr_pips = max(8.0, float(row["atr_pips"])) if pd.notna(row["atr_pips"]) else 12.0
        stop_pips = round(atr_pips * 1.5, 1)
        
        # Long Entry Condition: Trend Alignment + Micro Momentum Surge
        if (row["close"] > row["ema20"] > row["ema50"] > row["ema200"] and 
            prev["close"] <= prev["ema9"] and row["close"] > row["ema9"] and
            row["close"] > row["open"]):
            signals.append({
                "timestamp": timestamp,
                "side": "BUY",
                "stop_pips": stop_pips
            })
            
        # Short Entry Condition: Downtrend Alignment + Rejection
        elif (row["close"] < row["ema20"] < row["ema50"] < row["ema200"] and 
              prev["close"] >= prev["ema9"] and row["close"] < row["ema9"] and
              row["close"] < row["open"]):
            signals.append({
                "timestamp": timestamp,
                "side": "SELL",
                "stop_pips": stop_pips
            })
            
    return pd.DataFrame(signals)

def main():
    print("=" * 135)
    print(" 🚀 OANDA M1 FIXED-R BACKTEST ENGINE (0.50% RISK | +1.5R TARGET | -1.0R STOP | 30-MIN TIMEOUT)")
    print("=" * 135)
    
    results = {}
    all_trades: List[harness.TradeRecord] = []
    
    for sym in PAIRS:
        fpath = find_m1_file(sym)
        if not fpath:
            print(f"[-] {sym:<10}: No M1 CSV data discovered in search paths.")
            continue
            
        try:
            df = pd.read_csv(fpath)
            time_col = next((c for c in df.columns if c.lower() in ["timestamp", "time", "date", "datetime"]), None)
            if not time_col:
                print(f"[-] {sym:<10}: Missing timestamp column in {fpath.name}")
                continue
                
            df[time_col] = pd.to_datetime(df[time_col], utc=True)
            df = df.sort_values(time_col).set_index(time_col)
            
            # Normalize column names
            df.columns = [c.lower() for c in df.columns]
            
            # Generate signals
            sig_df = generate_microbeast_m1_signals(df, sym)
            if sig_df.empty:
                print(f"[-] {sym:<10}: 0 signals generated across {len(df)} M1 bars.")
                continue
                
            # Execute Fixed-R Harness
            trades, metrics = harness.run_fixed_r_harness(sym, df, sig_df)
            if metrics:
                results[sym] = metrics
                all_trades.extend(trades)
                print(f"[+] {sym:<10}: Processed {len(df):,d} bars -> {metrics['trades']:4d} trades | Return: {metrics['net_return_pct']:>+6.2f}% | PF: {metrics['pf']:.2f} | Max DD: {metrics['max_dd_pct']:.2f}%")
        except Exception as e:
            print(f"[!] Error processing {sym}: {e}")

    if results:
        print()
        harness.print_performance_table(results)
        
        # Portfolio Aggregate Summary
        tot_trades = sum(m["trades"] for m in results.values())
        tot_wins = sum(int(m["trades"] * m["win_rate"] / 100.0) for m in results.values())
        all_r = [t.r_multiple for t in all_trades]
        all_timeouts = [t for t in all_trades if t.exit_reason == "TIMEOUT_30M"]
        all_targets = [t for t in all_trades if t.exit_reason == "TARGET_HIT"]
        all_stops = [t for t in all_trades if t.exit_reason == "STOP_HIT"]
        
        print("\n" + "=" * 80)
        print(" 📊 PORTFOLIO COMPOSITE METRICS")
        print("=" * 80)
        print(f" Total Trades:          {tot_trades}")
        print(f" Composite Win Rate:    {tot_wins / tot_trades * 100:.2f}%")
        print(f" Mean Expectancy (R):   {np.mean(all_r):+.3f}R")
        print(f" Target Hits (+1.5R):   {len(all_targets)} ({len(all_targets)/tot_trades*100:.1f}%)")
        print(f" Stop Hits (-1.0R):     {len(all_stops)} ({len(all_stops)/tot_trades*100:.1f}%)")
        print(f" Timeouts (30-Min):     {len(all_timeouts)} ({len(all_timeouts)/tot_trades*100:.1f}%) | Avg Timeout R: {np.mean([t.r_multiple for t in all_timeouts]):+.3f}R")
        print("=" * 80)
    else:
        print("\n[!] No pairs were evaluated. Verify OANDA M1 candle data location.")

if __name__ == "__main__":
    main()
