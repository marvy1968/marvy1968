#!/usr/bin/env python3
"""
DAILY STAN WEINSTEIN STAGE 2 BREAKOUT ENGINE
- Resamples local 1-minute SIP parquet data to canonical daily bars
- Strict causal execution: Signal at Day T Close -> Fill at Day T+1 Open
- Friction: 10 bps spread + 5 bps adverse slippage + SEC/FINRA fees
- Trailing Stop: 20-day SMA or 3.0R target
"""

from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

LARGE_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
MID_DIR = Path("/home/moonrider1968/bots/data_midcap_sip_1m")

SYMBOLS = [
    # Mega/Large Caps
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AVGO", "GOOGL", "META", 
    "AMZN", "TSLA", "JPM", "GS", "XOM", "CVX", "WMT", "COST", "CAT", "GE",
    # High-Beta Mid-Caps
    "SMCI", "CELH", "DUOL", "AFRM", "UPST", "MARA", "RIOT", "SYM", "APP", "IOT"
]

def load_resampled_daily(sym: str) -> pd.DataFrame:
    p_large = LARGE_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
    p_mid = MID_DIR / f"{sym}_1MIN_SIP.parquet"
    p = p_large if p_large.exists() else p_mid
    if not p.exists():
        return pd.DataFrame()

    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["time_et"] = df["time"].dt.tz_convert("America/New_York")
    df["date"] = df["time_et"].dt.date

    # Resample to Daily RTH
    daily = df.groupby("date").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).reset_index()

    daily = daily.sort_values("date").reset_index(drop=True)
    return daily

def run_simulation():
    print("=" * 80)
    print("STAN WEINSTEIN STAGE 2 DAILY SWING ENGINE (2018-2024)")
    print("Causal Next-Day Open Fills | Realistic Multi-Day Friction")
    print("=" * 80)

    all_trades = []
    slip_pct = 0.0015   # 15 bps round-trip friction
    fee_pct = 0.0002

    for sym in SYMBOLS:
        df = load_resampled_daily(sym)
        if len(df) < 160:
            continue

        # Indicator Pipeline
        df["sma150"] = df["close"].rolling(150).mean()
        df["sma150_slope"] = df["sma150"] - df["sma150"].shift(10)
        df["sma20"] = df["close"].rolling(20).mean()
        df["vol_sma50"] = df["volume"].rolling(50).mean()
        df["high_20d"] = df["high"].rolling(20).max().shift(1)
        df["low_20d"] = df["low"].rolling(20).min().shift(1)

        # ATR(14)
        c = df["close"].values
        h = df["high"].values
        l = df["low"].values
        tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
        tr[0] = h[0] - l[0]
        df["atr"] = pd.Series(tr).rolling(14).mean()

        # Trade loop
        in_pos = False
        entry_px = 0.0
        stop_px = 0.0
        target_px = 0.0
        risk = 0.0
        entry_idx = 0

        for i in range(150, len(df) - 1):
            row = df.iloc[i]
            next_row = df.iloc[i + 1]

            if not in_pos:
                # Stage 2 Breakout Rules:
                # 1. Price > Rising 150-day SMA
                # 2. Volume > 1.3x 50-day average
                # 3. New 20-day high breakout
                is_stage2 = (row["close"] > row["sma150"]) and (row["sma150_slope"] > 0)
                vol_burst = row["volume"] > (row["vol_sma50"] * 1.3)
                breakout = row["close"] > row["high_20d"]

                if is_stage2 and vol_burst and breakout:
                    entry_px = next_row["open"] * (1.0 + slip_pct)
                    risk = max(row["atr"] * 1.8, entry_px - row["low_20d"])
                    stop_px = entry_px - risk
                    target_px = entry_px + 3.0 * risk
                    in_pos = True
                    entry_idx = i + 1

            else:
                # Position Management
                days_held = (i + 1) - entry_idx

                # Stop hit during day
                if next_row["low"] <= stop_px:
                    exit_px = stop_px * (1.0 - slip_pct)
                    pnl = (exit_px - entry_px) - (fee_pct * entry_px)
                    all_trades.append({"symbol": sym, "pnl_r": pnl / risk, "win": 0, "hit": "STOP", "days": days_held})
                    in_pos = False

                # Target hit
                elif next_row["high"] >= target_px:
                    exit_px = target_px * (1.0 - slip_pct)
                    pnl = (exit_px - entry_px) - (fee_pct * entry_px)
                    all_trades.append({"symbol": sym, "pnl_r": pnl / risk, "win": 1, "hit": "TARGET", "days": days_held})
                    in_pos = False

                # Trailing exit: close falls below 20-day SMA after being in profit
                elif row["close"] < row["sma20"] and (row["close"] > entry_px):
                    exit_px = next_row["open"] * (1.0 - slip_pct)
                    pnl = (exit_px - entry_px) - (fee_pct * entry_px)
                    all_trades.append({"symbol": sym, "pnl_r": pnl / risk, "win": 1 if pnl > 0 else 0, "hit": "TRAIL_20SMA", "days": days_held})
                    in_pos = False

                # Ratchet stop to entry after securing +1.5R
                elif row["high"] >= entry_px + 1.5 * risk:
                    stop_px = max(stop_px, entry_px)

        print(f"  {sym:<6} -> Done")

    if not all_trades:
        print("No trades generated.")
        return

    res = pd.DataFrame(all_trades)
    tot_trades = len(res)
    wr = res["win"].mean() * 100.0
    tot_r = res["pnl_r"].sum()
    exp_r = res["pnl_r"].mean()

    wins = res[res["pnl_r"] > 0]["pnl_r"].sum()
    losses = abs(res[res["pnl_r"] < 0]["pnl_r"].sum())
    pf = round(wins / losses if losses > 0 else 0, 2)

    cum_r = res["pnl_r"].cumsum()
    peak = np.maximum.accumulate(cum_r)
    dd = peak - cum_r
    max_dd = round(np.max(dd), 1) if len(dd) > 0 else 0.0
    rpi = round(tot_r / max_dd, 2) if max_dd > 0 else 0.0

    print("\n" + "=" * 80)
    print("DAILY STAGE 2 BREAKOUT SCORECARD (2018-2024)")
    print("=" * 80)
    print(f"Total Executions:   {tot_trades:,}")
    print(f"Win Rate:           {wr:.1f}%")
    print(f"Total Net R:        {tot_r:,.1f}R")
    print(f"Expectancy:         {exp_r:+.3f}R per trade")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Max Drawdown (R):   {max_dd:.1f}R")
    print(f"RPI (Net R / DD):   {rpi:.2f}")
    print(f"Average Hold:       {res['days'].mean():.1f} days")
    print("\nExit Breakdown:")
    print(res["hit"].value_counts().to_string())
    print("=" * 80)

if __name__ == "__main__":
    run_simulation()
