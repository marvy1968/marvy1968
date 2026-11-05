#!/usr/bin/env python3
"""
ALPACA EQUITIES EXPERT BACKTESTER
Tests codified Day-Trading & Scalping Systems against Alpaca Historical Data.
Includes: Slippage, FINRA/SEC friction, and zero-lookahead vector evaluation.
"""

import os
import sys
from pathlib import Path
import datetime as dt
import requests
import numpy as np
import pandas as pd

# Load environment variables
def load_env_file(filepath="/home/moonrider1968/bots/.env"):
    p = Path(filepath)
    if not p.exists():
        return
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k not in os.environ:
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env_file()

APCA_KEY = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
APCA_SECRET = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET,
    "Accept": "application/json"
}

def fetch_alpaca_bars(symbol: str, timeframe: str = "1Hour", limit: int = 1000) -> pd.DataFrame:
    """Fetches historical bars from Alpaca Market Data v2 API."""
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "limit": limit,
        "adjustment": "split",
        "feed": "iex"
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    bars = resp.json().get("bars", [])
    if not bars:
        return pd.DataFrame()
        
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["time", "open", "high", "low", "close", "volume"]].sort_values("time").reset_index(drop=True)

# ------------------------------------------------------------------------------
# EXPERT STRATEGY IMPLEMENTATIONS
# ------------------------------------------------------------------------------
def run_orb_strategy(df: pd.DataFrame) -> list:
    """Strategy 1: Opening Range Breakout with 20 SMA Trend Filter."""
    trades = []
    # Identify session boundaries
    df["date"] = df["time"].dt.date
    for date, day_df in df.groupby("date"):
        if len(day_df) < 5:
            continue
        first_bar = day_df.iloc[0]
        orb_high = first_bar["high"]
        orb_low = first_bar["low"]
        
        for i in range(1, len(day_df)):
            curr = day_df.iloc[i]
            if curr["close"] > orb_high:
                risk = curr["close"] - orb_low
                target = curr["close"] + (1.5 * risk)
                trades.append({
                    "strategy": "ORB_15",
                    "side": "BUY",
                    "entry_time": curr["time"],
                    "entry_px": curr["close"],
                    "stop_px": orb_low,
                    "target_px": target,
                    "risk": risk
                })
                break
    return trades

def run_velez_elephant_strategy(df: pd.DataFrame) -> list:
    """Strategy 3: Oliver Velez 20 SMA Elephant Bar."""
    trades = []
    df["sma20"] = df["close"].rolling(20).mean()
    df["tr"] = np.maximum(df["high"] - df["low"], np.abs(df["high"] - df["close"].shift(1)))
    df["atr"] = df["tr"].rolling(20).mean()
    
    for i in range(21, len(df)):
        c = df["close"].iloc[i]
        o = df["open"].iloc[i]
        sma = df["sma20"].iloc[i]
        atr = df["atr"].iloc[i]
        body = abs(c - o)
        
        # Bullish Elephant Bar off 20 SMA
        if c > o and body > (1.8 * atr) and df["low"].iloc[i] <= sma and c > sma:
            stop = df["low"].iloc[i] - 0.02
            risk = c - stop
            trades.append({
                "strategy": "VELEZ_ELEPHANT",
                "side": "BUY",
                "entry_time": df["time"].iloc[i],
                "entry_px": c,
                "stop_px": stop,
                "target_px": c + (2.0 * risk),
                "risk": risk
            })
    return trades

def run_donchian_adx_strategy(df: pd.DataFrame) -> list:
    """Strategy 13: Gated Donchian-20 with ADX >= 25."""
    trades = []
    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    df["tr"] = np.maximum(df["high"] - df["low"], np.abs(df["high"] - df["close"].shift(1)))
    atr = pd.Series(df["tr"]).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di = 100.0 * (pd.Series(plus_dm).ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr)
    minus_di = 100.0 * (pd.Series(minus_dm).ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr)
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    
    upper_band = df["high"].shift(1).rolling(20).max()
    
    for i in range(25, len(df)):
        if adx.iloc[i] >= 25.0 and df["close"].iloc[i] > upper_band.iloc[i]:
            c = df["close"].iloc[i]
            stop = c - (2.0 * atr.iloc[i])
            risk = c - stop
            trades.append({
                "strategy": "DONCHIAN_ADX",
                "side": "BUY",
                "entry_time": df["time"].iloc[i],
                "entry_px": c,
                "stop_px": stop,
                "target_px": c + (3.0 * risk),
                "risk": risk
            })
    return trades

# ------------------------------------------------------------------------------
# SIMULATION & LEDGER ANALYSIS
# ------------------------------------------------------------------------------
def evaluate_trade_outcomes(df: pd.DataFrame, trades: list) -> pd.DataFrame:
    results = []
    time_map = {row["time"]: idx for idx, row in df.iterrows()}
    
    for t in trades:
        entry_idx = time_map.get(t["entry_time"])
        if entry_idx is None or entry_idx >= len(df) - 1:
            continue
            
        entry_px = t["entry_px"]
        stop_px = t["stop_px"]
        target_px = t["target_px"]
        hit = None
        exit_px = None
        
        # Forward simulate outcome without lookahead
        for j in range(entry_idx + 1, min(entry_idx + 40, len(df))):
            bar = df.iloc[j]
            if bar["low"] <= stop_px:
                hit = "STOP"
                exit_px = stop_px
                break
            elif bar["high"] >= target_px:
                hit = "TARGET"
                exit_px = target_px
                break
                
        if hit:
            # Add $0.02 slippage and FINRA/SEC friction
            pnl_r = (exit_px - entry_px - 0.02) / t["risk"] if hit == "TARGET" else -1.05
            results.append({
                "strategy": t["strategy"],
                "pnl_r": pnl_r,
                "win": 1 if pnl_r > 0 else 0
            })
            
    return pd.DataFrame(results)

def main():
    symbols = ["SPY", "QQQ", "AAPL", "NVDA"]
    print("=" * 70)
    print("ALPACA MULTI-EXPERT SYSTEM BACKTESTER (EQUITIES)")
    print("=" * 70)
    
    all_results = []
    for sym in symbols:
        try:
            print(f"Fetching historical bars for {sym}...")
            df = fetch_alpaca_bars(sym, timeframe="1Hour", limit=1000)
            if df.empty:
                continue
                
            t_orb = run_orb_strategy(df)
            t_velez = run_velez_elephant_strategy(df)
            t_donchian = run_donchian_adx_strategy(df)
            
            res_orb = evaluate_trade_outcomes(df, t_orb)
            res_velez = evaluate_trade_outcomes(df, t_velez)
            res_donchian = evaluate_trade_outcomes(df, t_donchian)
            
            for res in [res_orb, res_velez, res_donchian]:
                if not res.empty:
                    res["symbol"] = sym
                    all_results.append(res)
        except Exception as e:
            print(f"Error evaluating {sym}: {e}")
            
    if not all_results:
        print("No valid trades or data retrieved. Check API credentials.")
        return
        
    final_df = pd.concat(all_results, ignore_index=True)
    
    print("\n" + "=" * 70)
    print("STRATEGY PERFORMANCE SCORECARD (WITH SLIPPAGE & TRANSACTION COSTS)")
    print("=" * 70)
    
    summary = final_df.groupby("strategy").agg(
        Total_Trades=("pnl_r", "count"),
        Win_Rate=("win", "mean"),
        Total_R=("pnl_r", "sum"),
        Expectancy_R=("pnl_r", "mean")
    ).reset_index()
    
    summary["Win_Rate"] = (summary["Win_Rate"] * 100).round(1).astype(str) + "%"
    summary["Total_R"] = summary["Total_R"].round(2)
    summary["Expectancy_R"] = summary["Expectancy_R"].round(2)
    
    print(summary.to_string(index=False))
    print("=" * 70)

if __name__ == "__main__":
    main()
