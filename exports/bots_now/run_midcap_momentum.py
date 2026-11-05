#!/usr/bin/env python3
"""
HIGH-BETA SMALL/MID-CAP MOMENTUM ENGINE
Execution: Next-bar Open with wide-spread slippage modeling (25 bps one-way)
"""

from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data_midcap_sip_1m")
DEV_START = pd.Timestamp("2021-01-04 00:00:00", tz="UTC")
DEV_END = pd.Timestamp("2024-12-31 23:59:59", tz="UTC")

SYMBOLS = ["SMCI", "CELH", "DUOL", "AFRM", "UPST", "MARA", "RIOT", "SYM", "APP", "IOT"]

def run_backtest():
    all_trades = []
    
    # Small/mid-cap realistic friction: 15 bps spread + 10 bps adverse slippage = 25 bps
    slip_pct = 0.0025
    fee_pct = 0.0002

    print("=" * 80)
    print("BACKTESTING HIGH-BETA MID-CAP INTRADAY EXPANSION (2021-2024)")
    print(f"Universe: {len(SYMBOLS)} Momentum Equities | Friction: 25 bps Adverse Drag")
    print("=" * 80)

    for sym in SYMBOLS:
        p = DATA_DIR / f"{sym}_1MIN_SIP.parquet"
        if not p.exists():
            continue

        df = pd.read_parquet(p)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df[(df["time"] >= DEV_START) & (df["time"] <= DEV_END)].copy()
        df["time_et"] = df["time"].dt.tz_convert("America/New_York")
        df["date"] = df["time_et"].dt.date

        sym_trades = []
        for d, day_df in df.groupby("date"):
            n = len(day_df)
            if n < 40:
                continue

            c = day_df["close"].values
            h = day_df["high"].values
            l = day_df["low"].values
            o = day_df["open"].values
            v = day_df["volume"].values
            t_et = day_df["time_et"].dt.time.values

            orb_15_h = np.max(h[:15])
            orb_15_l = np.min(l[:15])
            orb_range = orb_15_h - orb_15_l

            # Volatility threshold: require >= 1.5% opening range expansion
            if (orb_range / c[15]) < 0.015:
                continue

            # Breakout at bar 16 (09:46 ET)
            if c[16] > orb_15_h and v[16] > np.mean(v[:15]) * 1.2:
                entry_px = o[17] * (1.0 + slip_pct)
                stop_px = orb_15_l
                risk = entry_px - stop_px

                if risk <= 0.05:
                    continue

                target_px = entry_px + 2.5 * risk
                exit_px = None
                hit = "EOD"

                for j in range(18, n):
                    if t_et[j] >= dt.time(15, 55):
                        exit_px = c[j] * (1.0 - slip_pct)
                        hit = "EOD"
                        break
                    if l[j] <= stop_px:
                        exit_px = stop_px * (1.0 - slip_pct)
                        hit = "STOP"
                        break
                    elif h[j] >= target_px:
                        exit_px = target_px * (1.0 - slip_pct)
                        hit = "TARGET"
                        break

                if exit_px is None:
                    exit_px = c[-1] * (1.0 - slip_pct)

                net_pnl = (exit_px - entry_px) - (fee_pct * entry_px)
                pnl_r = net_pnl / risk
                sym_trades.append({"symbol": sym, "pnl_r": pnl_r, "win": 1 if pnl_r > 0 else 0, "hit": hit})

        all_trades.extend(sym_trades)
        print(f"  {sym:<6} -> Logged Trades: {len(sym_trades):>4}")

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
    print("HIGH-BETA MID-CAP SCORECARD (2021-2024)")
    print("=" * 80)
    print(f"Total Executions:   {tot_trades:,}")
    print(f"Win Rate:           {wr:.1f}%")
    print(f"Total Net R:        {tot_r:,.1f}R")
    print(f"Expectancy:         {exp_r:+.3f}R per trade")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Max Drawdown (R):   {max_dd:.1f}R")
    print(f"RPI (Net R / DD):   {rpi:.2f}")
    print("\nExit Breakdown:")
    print(res["hit"].value_counts().to_string())
    print("=" * 80)

if __name__ == "__main__":
    run_backtest()
