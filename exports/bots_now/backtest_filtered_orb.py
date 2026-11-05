#!/usr/bin/env python3
"""
HIGH-EXPECTANCY ORB ENGINE — REGIME & RELATIVE STRENGTH ENHANCEMENT
Evaluates whether rVOL and Relative Strength to SPY filters rescue ORB 15M.
"""

import time
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
DEV_START = pd.Timestamp("2017-01-03 00:00:00", tz="UTC")
DEV_END = pd.Timestamp("2022-12-30 23:59:59", tz="UTC")

SYMBOLS = [
    "QQQ", "AAPL", "MSFT", "NVDA", "AVGO",
    "GOOGL", "META", "AMZN", "TSLA", "JPM", "GS",
    "XOM", "CVX", "WMT", "COST", "CAT", "GE"
]

def simulate_engine(df_sym: pd.DataFrame, df_spy: pd.DataFrame, sym: str):
    trades_raw = []
    trades_filtered = []

    # Map dates
    sym_by_date = dict(tuple(df_sym.groupby("date")))
    spy_by_date = dict(tuple(df_spy.groupby("date")))
    common_dates = sorted(list(set(sym_by_date.keys()).intersection(spy_by_date.keys())))

    slip_pct = 0.00015
    fee_pct = 0.0002

    for d in common_dates:
        s_df = sym_by_date[d]
        m_df = spy_by_date[d]
        n = len(s_df)
        if n < 60 or len(m_df) < 60:
            continue

        s_c = s_df["close"].values
        s_h = s_df["high"].values
        s_l = s_df["low"].values
        s_o = s_df["open"].values
        s_v = s_df["volume"].values
        s_times = s_df["time_et"].dt.time.values

        m_c = m_df["close"].values
        m_o = m_df["open"].values

        # 15M ORB setup
        orb_h = np.max(s_h[:15])
        orb_l = np.min(s_l[:15])
        orb_range = orb_h - orb_l
        if orb_range <= 0.05:
            continue

        # Look for breakout bar at i=16 (09:46 ET)
        i = 16
        if s_c[i] > orb_h:
            # 1. Base Strategy Trade
            entry_px = s_o[i+1] * (1.0 + slip_pct)
            stop_px = orb_l
            risk = entry_px - stop_px
            if risk > 0.05:
                target_px = entry_px + 2.0 * risk
                # Trace exit
                for j in range(i + 2, n):
                    if s_times[j] >= dt.time(15, 55):
                        pnl = (s_c[j] * (1.0 - slip_pct) - entry_px) - (fee_pct * entry_px)
                        trades_raw.append({"pnl_r": pnl / risk, "win": 1 if pnl > 0 else 0})
                        break
                    if s_l[j] <= stop_px:
                        pnl = (stop_px * (1.0 - slip_pct) - entry_px) - (fee_pct * entry_px)
                        trades_raw.append({"pnl_r": pnl / risk, "win": 0})
                        break
                    elif s_h[j] >= target_px:
                        pnl = (target_px * (1.0 - slip_pct) - entry_px) - (fee_pct * entry_px)
                        trades_raw.append({"pnl_r": pnl / risk, "win": 1})
                        break

            # 2. Enhanced Strategy Gating:
            # Filter A: rVOL_15 > 1.8 (relative to opening volume of the day)
            rvol_pass = s_v[i] > (np.mean(s_v[:15]) * 1.8)

            # Filter B: Mansfield Relative Strength > SPY at the breakout
            sym_ret_15 = (s_c[i] - s_o[0]) / s_o[0]
            spy_ret_15 = (m_c[min(i, len(m_c)-1)] - m_o[0]) / m_o[0]
            rs_pass = sym_ret_15 > (spy_ret_15 + 0.002)  # Outperforming SPY by 20 bps

            # Filter C: Narrow ORB Risk (prevent chasing outsized 15m whipsaws)
            risk_pct = (orb_range / s_c[i])
            sensible_range = 0.003 <= risk_pct <= 0.020

            if rvol_pass and rs_pass and sensible_range and risk > 0.05:
                target_px_enh = entry_px + 2.0 * risk
                cur_stop = stop_px
                for j in range(i + 2, n):
                    # Time-decay breakeven ratcheting after 30 mins
                    if j == i + 32 and (s_c[j] - entry_px) > 0.5 * risk:
                        cur_stop = entry_px  # Protect capital

                    if s_times[j] >= dt.time(15, 55):
                        pnl = (s_c[j] * (1.0 - slip_pct) - entry_px) - (fee_pct * entry_px)
                        trades_filtered.append({"pnl_r": pnl / risk, "win": 1 if pnl > 0 else 0})
                        break
                    if s_l[j] <= cur_stop:
                        pnl = (cur_stop * (1.0 - slip_pct) - entry_px) - (fee_pct * entry_px)
                        trades_filtered.append({"pnl_r": pnl / risk, "win": 1 if pnl > 0 else 0})
                        break
                    elif s_h[j] >= target_px_enh:
                        pnl = (target_px_enh * (1.0 - slip_pct) - entry_px) - (fee_pct * entry_px)
                        trades_filtered.append({"pnl_r": pnl / risk, "win": 1})
                        break

    return trades_raw, trades_filtered

def main():
    print("=" * 80)
    print("BACKTESTING HIGH-EXPECTANCY ENHANCEMENTS ON DEVELOPMENT SET (2017-2022)")
    print("Testing: Base 02_ORB_15M vs Enhanced (rVOL + RS-to-SPY + Breakeven Decay)")
    print("=" * 80)

    f_spy = DATA_DIR / "SPY_1MIN_SIP_2017_2026.parquet"
    df_spy = pd.read_parquet(f_spy)
    df_spy["time"] = pd.to_datetime(df_spy["time"], utc=True)
    df_spy = df_spy[(df_spy["time"] >= DEV_START) & (df_spy["time"] <= DEV_END)].copy()
    df_spy["time_et"] = df_spy["time"].dt.tz_convert("America/New_York")
    df_spy["date"] = df_spy["time_et"].dt.date

    raw_total = []
    enh_total = []
    t0 = time.time()

    for sym in SYMBOLS:
        f = DATA_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df_dev = df[(df["time"] >= DEV_START) & (df["time"] <= DEV_END)].copy()
        del df
        df_dev["time_et"] = df_dev["time"].dt.tz_convert("America/New_York")
        df_dev["date"] = df_dev["time_et"].dt.date

        t_raw, t_enh = simulate_engine(df_dev, df_spy, sym)
        raw_total.extend(t_raw)
        enh_total.extend(t_enh)
        print(f"  {sym:<6} | Base Trades: {len(t_raw):>4} | Filtered High-Grade: {len(t_enh):>4}")

    dur = time.time() - t0
    print("\n" + "=" * 80)
    print(f"RESULTS COMPARISON (2017-2022 DEV SET) [{dur:.1f}s]")
    print("=" * 80)

    for label, data in [("Base 02_ORB_15M (Unfiltered)", raw_total), ("Enhanced ORB (RS + rVOL + Decay)", enh_total)]:
        if not data:
            continue
        df_res = pd.DataFrame(data)
        trades = len(df_res)
        wr = df_res["win"].mean() * 100
        tot_r = df_res["pnl_r"].sum()
        exp_r = df_res["pnl_r"].mean()
        wins = df_res[df_res["pnl_r"] > 0]["pnl_r"].sum()
        losses = abs(df_res[df_res["pnl_r"] < 0]["pnl_r"].sum())
        pf = round(wins / losses if losses > 0 else 0, 2)

        # Max Drawdown in R
        cum_r = df_res["pnl_r"].cumsum()
        peak = np.maximum.accumulate(cum_r)
        dd = peak - cum_r
        max_dd_r = round(np.max(dd), 1) if len(dd) > 0 else 0.0
        rpi = round(tot_r / max_dd_r, 2) if max_dd_r > 0 else 0.0

        print(f"{label:<36} | Trades: {trades:>5} | WinRate: {wr:>5.1f}% | Tot_R: {tot_r:>7.1f}R | Exp_R: {exp_r:>6.3f}R | PF: {pf:>4.2f} | MaxDD: {max_dd_r:>5.1f}R | RPI: {rpi:>5.2f}")
    print("=" * 80)

if __name__ == "__main__":
    main()
