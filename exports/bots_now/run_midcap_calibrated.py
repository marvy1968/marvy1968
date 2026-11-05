#!/usr/bin/env python3
"""
CALIBRATED HIGH-BETA MID-CAP ENGINE
- Risk anchored to 1.0 * ATR(14) rather than wide 15m range low
- Attainable 1.5R target matched to morning volatility expansion
- Midday 12:00 ET time-stop to prevent afternoon drift
- Realistic mid-cap friction: 25 bps one-way drag
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
    slip_pct = 0.0025  # 25 bps friction
    fee_pct = 0.0002

    print("=" * 80)
    print("CALIBRATED MID-CAP MORNING EXPANSION (2021-2024)")
    print("Parameters: 1.0*ATR Stop | 1.5R Target | 12:00 ET Midday Exit")
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

            # Volatility check: >= 1.2% opening expansion
            if ((orb_15_h - orb_15_l) / c[15]) < 0.012:
                continue

            # Breakout at bar 16 (09:46 ET)
            if c[16] > orb_15_h and v[16] > np.mean(v[:15]) * 1.2:
                # Compute ATR(14)
                tr = np.maximum(h[:17] - l[:17], np.maximum(np.abs(h[:17] - np.roll(c[:17], 1)), np.abs(l[:17] - np.roll(c[:17], 1))))
                tr[0] = h[0] - l[0]
                atr = np.mean(tr[-14:])

                entry_px = o[17] * (1.0 + slip_pct)
                risk = 1.0 * atr
                if risk <= 0.05:
                    continue

                stop_px = entry_px - risk
                target_px = entry_px + 1.5 * risk
                exit_px = None
                hit = "MIDDAY"

                for j in range(18, n):
                    # Exit at 12:00 ET if target/stop not reached
                    if t_et[j] >= dt.time(12, 0):
                        exit_px = c[j] * (1.0 - slip_pct)
                        hit = "MIDDAY"
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
    print("CALIBRATED SCORECARD (2021-2024)")
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
