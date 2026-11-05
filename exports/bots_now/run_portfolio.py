#!/usr/bin/env python3
"""
OPTIMIZED PORTFOLIO ENGINE — CANONICAL SIP 1-MIN
Refinements:
  - SPY Intraday Regime Gate (Only trade long when SPY > VWAP)
  - Midpoint/ATR Structural Stop (Tighter R denominator)
  - 50% Scale-Out at +1.0R to Lock Friction-Free Gains
  - Dynamic Half-Kelly Position Sizing
"""

import argparse
import sys
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

SECTOR_MAP = {
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH", 
    "GOOGL": "TECH", "META": "TECH", "AMZN": "TECH", "TSLA": "TECH",
    "JPM": "FIN", "GS": "FIN",
    "XOM": "ENERGY", "CVX": "ENERGY",
    "WMT": "STAPLES", "COST": "STAPLES",
    "CAT": "IND", "GE": "IND",
    "SPY": "INDEX", "QQQ": "INDEX"
}

def load_canonical_data(data_dir: Path, symbol: str, start: str, end: str) -> pd.DataFrame:
    p = data_dir / f"{symbol}_1MIN_SIP_2017_2026.parquet"
    if not p.exists():
        raise FileNotFoundError(f"Missing canonical file: {p}")
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    t_start = pd.Timestamp(start, tz="UTC")
    t_end = pd.Timestamp(end, tz="UTC")
    df = df[(df["time"] >= t_start) & (df["time"] <= t_end)].copy()
    df["time_et"] = df["time"].dt.tz_convert("America/New_York")
    df["date"] = df["time_et"].dt.date
    return df

def run_portfolio_sim(data_dir: Path, symbols: list[str], start_date: str, end_date: str, initial_nav: float = 100000.0):
    print("=" * 80)
    print("RUNNING REFINED PORTFOLIO ENGINE")
    print(f"Window: {start_date} -> {end_date} | Universe: {len(symbols)} Equities")
    print("=" * 80)

    # 1. Ingest Data
    symbol_data = {}
    for sym in symbols:
        try:
            symbol_data[sym] = load_canonical_data(data_dir, sym, start_date, end_date)
        except Exception as e:
            print(f"  [WARN] Skipping {sym}: {e}")

    spy_df = symbol_data.get("SPY")
    assert spy_df is not None, "SPY data required for macro regime filter."

    # Index SPY by date for quick lookup
    spy_by_date = dict(tuple(spy_df.groupby("date")))
    all_dates = sorted(list(set.union(*[set(df["date"].unique()) for sym, df in symbol_data.items() if sym != "SPY"])))

    nav = initial_nav
    equity_curve = []
    closed_trades = []
    kelly_risk_pct = 0.005  # 0.50% NAV max risk per position

    slip_rate = 0.00015
    fee_rate = 0.0002

    for d in all_dates:
        if d not in spy_by_date:
            continue

        s_spy = spy_by_date[d]
        if len(s_spy) < 20:
            continue

        # SPY Intraday Regime Filter: Only trade if SPY is above its opening VWAP at 09:46
        spy_c = s_spy["close"].values
        spy_v = s_spy["volume"].values
        spy_tp = (s_spy["high"].values + s_spy["low"].values + spy_c) / 3.0
        spy_vwap_16 = np.sum(spy_tp[:16] * spy_v[:16]) / np.sum(spy_v[:16])
        if spy_c[16] < spy_vwap_16:
            # Market broad trend is down; skip long breakouts today
            equity_curve.append({"date": d, "nav": nav})
            continue

        # Scan equities for breakout
        candidates = []
        for sym, df in symbol_data.items():
            if sym in ["SPY", "QQQ"]:
                continue  # Trade individual equities for beta expansion
            
            sub = df[df["date"] == d]
            if len(sub) < 60:
                continue

            c = sub["close"].values
            h = sub["high"].values
            l = sub["low"].values
            o = sub["open"].values

            orb_h = np.max(h[:15])
            orb_l = np.min(l[:15])
            rng = orb_h - orb_l

            # Check breakout at bar 16 (09:46 ET)
            if c[16] > orb_h and (rng / c[16]) >= 0.004:
                ret_15 = (c[16] - o[0]) / o[0]
                candidates.append({
                    "symbol": sym,
                    "sector": SECTOR_MAP.get(sym, "OTHER"),
                    "entry_raw": o[17],
                    "orb_h": orb_h,
                    "orb_l": orb_l,
                    "midpoint": orb_h - (0.5 * rng),
                    "ret_15": ret_15,
                    "day_df": sub
                })

        candidates.sort(key=lambda x: x["ret_15"], reverse=True)

        # Sector & Position Capacity (Max 2 per sector, max 3 concurrent)
        open_positions = []
        active_sectors = {}
        for cand in candidates:
            if len(open_positions) >= 3:
                break
            sec = cand["sector"]
            if active_sectors.get(sec, 0) >= 1:
                continue
            active_sectors[sec] = 1
            open_positions.append(cand)

        for pos in open_positions:
            day_df = pos["day_df"]
            c = day_df["close"].values
            h = day_df["high"].values
            l = day_df["low"].values
            t_et = day_df["time_et"].dt.time.values
            n_bars = len(day_df)

            entry_px = pos["entry_raw"] * (1.0 + slip_rate)
            # Tighter stop: Midpoint of opening range instead of full range low
            stop_px = pos["midpoint"]
            risk_per_share = entry_px - stop_px
            if risk_per_share <= 0.02:
                continue

            dollar_risk = nav * kelly_risk_pct
            shares = int(dollar_risk / risk_per_share)
            if shares < 1:
                continue

            target_1 = entry_px + 1.0 * risk_per_share
            target_2 = entry_px + 2.0 * risk_per_share

            # Position scaling logic
            shares_rem = shares
            cur_stop = stop_px
            trade_pnl = 0.0

            for j in range(18, n_bars):
                # EOD Square-off
                if t_et[j] >= dt.time(15, 55):
                    exit_px = c[j] * (1.0 - slip_rate)
                    trade_pnl += (exit_px - entry_px) * shares_rem - (fee_rate * exit_px * shares_rem)
                    shares_rem = 0
                    break

                # Stop loss hit
                if l[j] <= cur_stop:
                    exit_px = cur_stop * (1.0 - slip_rate)
                    trade_pnl += (exit_px - entry_px) * shares_rem - (fee_rate * exit_px * shares_rem)
                    shares_rem = 0
                    break

                # Scale 1: 50% off at +1.0R, stop moves to Breakeven
                if shares_rem == shares and h[j] >= target_1:
                    half_shares = shares // 2
                    exit_px = target_1 * (1.0 - slip_rate)
                    trade_pnl += (exit_px - entry_px) * half_shares - (fee_rate * exit_px * half_shares)
                    shares_rem -= half_shares
                    cur_stop = entry_px  # Risk eliminated on remainder

                # Scale 2: Remaining 50% at +2.0R
                elif shares_rem < shares and h[j] >= target_2:
                    exit_px = target_2 * (1.0 - slip_rate)
                    trade_pnl += (exit_px - entry_px) * shares_rem - (fee_rate * exit_px * shares_rem)
                    shares_rem = 0
                    break

            if shares_rem > 0:
                exit_px = c[-1] * (1.0 - slip_rate)
                trade_pnl += (exit_px - entry_px) * shares_rem - (fee_rate * exit_px * shares_rem)

            nav += trade_pnl
            closed_trades.append({
                "date": d,
                "symbol": pos["symbol"],
                "pnl_dollar": trade_pnl,
                "pnl_r": trade_pnl / dollar_risk
            })

        equity_curve.append({"date": d, "nav": nav})

    trades_df = pd.DataFrame(closed_trades)
    eq_df = pd.DataFrame(equity_curve)

    dur_trades = len(trades_df)
    tot_return_pct = ((nav - initial_nav) / initial_nav) * 100.0

    eq_df["peak"] = eq_df["nav"].cummax()
    eq_df["dd"] = (eq_df["peak"] - eq_df["nav"]) / eq_df["peak"]
    max_dd_pct = eq_df["dd"].max() * 100.0
    rpi = (tot_return_pct / max_dd_pct) if max_dd_pct > 0 else 0.0

    print("=" * 80)
    print("REFINED PORTFOLIO SCORECARD (VALIDATION: 2023-2024)")
    print("=" * 80)
    print(f"Starting NAV:       ${initial_nav:,.2f}")
    print(f"Ending NAV:         ${nav:,.2f}")
    print(f"Total Return:       {tot_return_pct:>7.2f}%")
    print(f"Max Drawdown:       {max_dd_pct:>7.2f}%")
    print(f"RPI (Return / DD):  {rpi:>7.2f}")
    print(f"Total Executions:   {dur_trades:,}")
    if dur_trades > 0:
        win_rate = (trades_df["pnl_r"] > 0).mean() * 100.0
        profit_factor = trades_df[trades_df["pnl_dollar"] > 0]["pnl_dollar"].sum() / abs(trades_df[trades_df["pnl_dollar"] < 0]["pnl_dollar"].sum())
        print(f"Win Rate:           {win_rate:>7.1f}%")
        print(f"Profit Factor:      {profit_factor:>7.2f}")
    print("=" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--data-dir", default="/home/moonrider1968/bots/data_alpaca_sip_1m")
    args = parser.parse_args()

    UNIVERSE = [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AVGO",
        "GOOGL", "META", "AMZN", "TSLA", "JPM", "GS",
        "XOM", "CVX", "WMT", "COST", "CAT", "GE"
    ]

    run_portfolio_sim(Path(args.data_dir), UNIVERSE, args.start, args.end)
