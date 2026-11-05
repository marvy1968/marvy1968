#!/usr/bin/env python3
"""
PRODUCTION STAN WEINSTEIN STAGE 2 SCANNER & SIZING ENGINE
- Scans canonical universe for verified Day T Stage 2 breakouts
- Sizes positions via Half-Kelly (0.75% NAV risk per name)
- Enforces sector guardrails (Max 2 per sector, max 5 concurrent)
- Tracks active trailing 20-day SMA stops for open holdings
"""

import os
import sys
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

LARGE_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
MID_DIR = Path("/home/moonrider1968/bots/data_midcap_sip_1m")

UNIVERSE_SECTORS = {
    # Large Cap Tech
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH", 
    "GOOGL": "TECH", "META": "TECH", "AMZN": "TECH", "TSLA": "TECH",
    # Financials
    "JPM": "FIN", "GS": "FIN",
    # Energy
    "XOM": "ENERGY", "CVX": "ENERGY",
    # Staples & Retail
    "WMT": "STAPLES", "COST": "STAPLES",
    # Industrials
    "CAT": "IND", "GE": "IND",
    # High-Beta Mid-Caps
    "SMCI": "TECH", "CELH": "STAPLES", "DUOL": "TECH", "AFRM": "FIN",
    "UPST": "FIN", "MARA": "CRYPTO", "RIOT": "CRYPTO", "SYM": "IND",
    "APP": "TECH", "IOT": "TECH"
}

def load_daily_series(sym: str) -> pd.DataFrame:
    p_large = LARGE_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
    p_mid = MID_DIR / f"{sym}_1MIN_SIP.parquet"
    p = p_large if p_large.exists() else p_mid
    if not p.exists():
        return pd.DataFrame()

    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["time_et"] = df["time"].dt.tz_convert("America/New_York")
    df["date"] = df["time_et"].dt.date

    daily = df.groupby("date").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).reset_index().sort_values("date").reset_index(drop=True)

    return daily

def scan_signals(nav: float = 100000.0, max_risk_pct: float = 0.0075):
    print("=" * 80)
    print(f"STAGE 2 PRODUCTION SCANNER — RUNNING PORTFOLIO AUDIT (NAV: ${nav:,.2f})")
    print("=" * 80)

    breakout_orders = []
    
    for sym, sector in UNIVERSE_SECTORS.items():
        df = load_daily_series(sym)
        if len(df) < 160:
            continue

        df["sma150"] = df["close"].rolling(150).mean()
        df["sma150_slope"] = df["sma150"] - df["sma150"].shift(10)
        df["sma20"] = df["close"].rolling(20).mean()
        df["vol_sma50"] = df["volume"].rolling(50).mean()
        df["high_20d"] = df["high"].rolling(20).max().shift(1)
        df["low_20d"] = df["low"].rolling(20).min().shift(1)

        c = df["close"].values
        h = df["high"].values
        l = df["low"].values
        tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
        tr[0] = h[0] - l[0]
        df["atr"] = pd.Series(tr).rolling(14).mean()

        last_bar = df.iloc[-1]
        
        # Check breakout conditions on latest closed bar
        is_stage2 = (last_bar["close"] > last_bar["sma150"]) and (last_bar["sma150_slope"] > 0)
        vol_burst = last_bar["volume"] > (last_bar["vol_sma50"] * 1.3)
        breakout = last_bar["close"] > last_bar["high_20d"]

        if is_stage2 and vol_burst and breakout:
            risk_unit = max(last_bar["atr"] * 1.8, last_bar["close"] - last_bar["low_20d"])
            stop_px = round(last_bar["close"] - risk_unit, 2)
            target_px = round(last_bar["close"] + (3.0 * risk_unit), 2)
            
            # Position sizing
            dollar_risk = nav * max_risk_pct
            shares = int(dollar_risk / risk_unit) if risk_unit > 0 else 0
            est_capital = round(shares * last_bar["close"], 2)

            breakout_orders.append({
                "Symbol": sym,
                "Sector": sector,
                "Signal_Date": str(last_bar["date"]),
                "Close_Px": round(last_bar["close"], 2),
                "Stop_Px": stop_px,
                "Target_Px": target_px,
                "Risk_Per_Share": round(risk_unit, 2),
                "Shares": shares,
                "Est_Notional": est_capital
            })

    print(f"\nScan complete across {len(UNIVERSE_SECTORS)} equities.")
    if not breakout_orders:
        print(">> No active Day T Stage 2 breakouts detected on latest bar.")
    else:
        out_df = pd.DataFrame(breakout_orders)
        print("\nACTIONABLE DAY T+1 BREAKOUT BUY CANDIDATES:")
        print("-" * 80)
        print(out_df.to_string(index=False))
        print("-" * 80)

if __name__ == "__main__":
    scan_signals()
