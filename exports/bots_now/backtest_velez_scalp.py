#!/usr/bin/env python3
"""
OLIVER VELEZ 15-MINUTE SCALP ENGINE (25% NAV ALLOCATION)
Rules:
  - Timeframe: 2-minute bars resampled from 1-min SIP tape
  - Filter: 20 EMA > 200 SMA (Long bias)
  - Trigger: Elephant Bar (Body >= 2.0x avg body, close near high, starts near 20 EMA)
  - Allocation: 25% of NAV notional value
  - Invalidation: Elephant Bar low
  - Target: 1.5R
  - Max Duration: Strict 15-minute time stop (exit on 8th 2-min bar)
"""

from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "QQQ", "SPY"]

START_DATE = pd.Timestamp("2023-01-03", tz="UTC")
END_DATE = pd.Timestamp("2024-12-31", tz="UTC")

def load_resampled_2m(sym: str) -> pd.DataFrame:
    p = DATA_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[(df["time"] >= START_DATE) & (df["time"] <= END_DATE)].copy()
    df["time_et"] = df["time"].dt.tz_convert("America/New_York")
    df["date"] = df["time_et"].dt.date
    
    # Resample to 2-min bars within RTH
    df = df.set_index("time")
    res = df.groupby("date").resample("2min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index(level=0, drop=True).reset_index()
    
    res["time_et"] = res["time"].dt.tz_convert("America/New_York")
    res["date"] = res["time_et"].dt.date
    return res

def run_simulation(initial_nav: float = 100000.0):
    print("=" * 80)
    print("OLIVER VELEZ 15-MINUTE SCALPING ENGINE (VALIDATION: 2023-2024)")
    print("Max Hold: 15 Mins | Allocation: 25% NAV | Friction: 6 bps + Fees")
    print("=" * 80)

    all_trades = []
    nav = initial_nav
    slip_rate = 0.0003  # 3 bps slippage
    fee_rate = 0.0002   # SEC/FINRA

    for sym in SYMBOLS:
        df = load_resampled_2m(sym)
        if len(df) < 300:
            continue

        # Technical indicators
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["sma200"] = df["close"].rolling(200).mean()
        
        # Candle bodies
        body = np.abs(df["close"] - df["open"])
        df["avg_body"] = body.rolling(20).mean()
        df["body"] = body
        
        # Elephant Bar signature:
        # 1. Bullish green bar
        # 2. Body >= 2.0x average body
        # 3. Close in top 15% of bar range (minimal upper wick)
        # 4. Originated within 0.4% of 20 EMA (Rubber band not stretched)
        bar_range = df["high"] - df["low"]
        upper_wick = df["high"] - df["close"]
        
        df["is_elephant"] = (
            (df["close"] > df["open"]) &
            (df["body"] >= 2.0 * df["avg_body"]) &
            (upper_wick <= 0.15 * bar_range) &
            (np.abs(df["open"] - df["ema20"]) / df["ema20"] <= 0.004) &
            (df["close"] > df["ema20"]) &
            (df["ema20"] > df["sma200"])
        )

        n = len(df)
        i = 200
        while i < n - 10:
            row = df.iloc[i]
            
            # Restrict entries between 09:40 and 15:30 ET
            t = row["time_et"].time()
            if t < dt.time(9, 40) or t > dt.time(15, 30):
                i += 1
                continue

            if row["is_elephant"]:
                next_row = df.iloc[i + 1]
                entry_px = next_row["open"] * (1.0 + slip_rate)
                stop_px = row["low"] * (1.0 - slip_rate)
                risk_per_share = entry_px - stop_px

                if risk_per_share <= 0.02:
                    i += 1
                    continue

                # 25% NAV Position Allocation
                notional_budget = nav * 0.25
                shares = int(notional_budget / entry_px)
                if shares < 1:
                    i += 1
                    continue

                target_px = entry_px + 1.5 * risk_per_share
                exit_px = None
                hit_reason = "15MIN_TIME_STOP"

                # Max 7 bars of 2-minute candles (~14-15 minutes max duration)
                max_bars = min(i + 8, n)
                for j in range(i + 1, max_bars):
                    bar_j = df.iloc[j]

                    # Stop out
                    if bar_j["low"] <= stop_px:
                        exit_px = stop_px * (1.0 - slip_rate)
                        hit_reason = "STOP"
                        break
                    # Target hit
                    elif bar_j["high"] >= target_px:
                        exit_px = target_px * (1.0 - slip_rate)
                        hit_reason = "TARGET"
                        break

                # If neither stop nor target hit within 15 mins -> liquidates at market
                if exit_px is None:
                    exit_px = df.iloc[max_bars - 1]["close"] * (1.0 - slip_rate)
                    hit_reason = "15MIN_TIME_STOP"

                pnl_dollars = ((exit_px - entry_px) * shares) - (fee_rate * entry_px * shares)
                pnl_r = pnl_dollars / (risk_per_share * shares)
                nav += pnl_dollars

                all_trades.append({
                    "symbol": sym,
                    "date": row["date"],
                    "pnl_dollars": pnl_dollars,
                    "pnl_r": pnl_r,
                    "win": 1 if pnl_dollars > 0 else 0,
                    "hit": hit_reason
                })

                # Skip past this trade duration
                i = max_bars
                continue

            i += 1

        print(f"  {sym:<6} -> Processed")

    if not all_trades:
        print("No trades triggered.")
        return

    res = pd.DataFrame(all_trades)
    tot_trades = len(res)
    wr = res["win"].mean() * 100.0
    tot_pnl = res["pnl_dollars"].sum()
    wins = res[res["pnl_dollars"] > 0]["pnl_dollars"].sum()
    losses = abs(res[res["pnl_dollars"] < 0]["pnl_dollars"].sum())
    pf = round(wins / losses if losses > 0 else 0, 2)

    print("\n" + "=" * 80)
    print("OLIVER VELEZ 15-MIN SCALP SCORECARD (2023-2024)")
    print("=" * 80)
    print(f"Starting NAV:       ${initial_nav:,.2f}")
    print(f"Ending NAV:         ${nav:,.2f}")
    print(f"Total Return:       {((nav - initial_nav)/initial_nav)*100.0:>7.2f}%")
    print(f"Total Executions:   {tot_trades:,}")
    print(f"Win Rate:           {wr:>7.1f}%")
    print(f"Profit Factor:      {pf:>7.2f}")
    print("\nExit Breakdown:")
    print(res["hit"].value_counts().to_string())
    print("=" * 80)

if __name__ == "__main__":
    run_simulation()
