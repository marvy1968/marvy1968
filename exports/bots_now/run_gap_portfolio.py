#!/usr/bin/env python3
"""
GAP-AND-GO PORTFOLIO ENGINE (POSITIVE-EXPECTANCY ANCHOR)
Partition: Validation Set (2023-01-03 to 2024-12-31)
Architecture:
  - Overnight Catalyst Gaps (>= 0.6% gap over prior day close)
  - Opening Range confirmation (15m close > 5m high)
  - Full ATR room to run (1.5 * ATR stop, no midpoint choke)
  - Asymmetric 2.5R Target + 15:55 EOD exit
  - Dynamic Half-Kelly Sizing
"""

import argparse
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
        raise FileNotFoundError(f"Missing file: {p}")
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    t_start = pd.Timestamp(start, tz="UTC")
    t_end = pd.Timestamp(end, tz="UTC")
    df = df[(df["time"] >= t_start) & (df["time"] <= t_end)].copy()
    df["time_et"] = df["time"].dt.tz_convert("America/New_York")
    df["date"] = df["time_et"].dt.date
    return df

def run_gap_sim(data_dir: Path, symbols: list[str], start_date: str, end_date: str, initial_nav: float = 100000.0):
    print("=" * 80)
    print("RUNNING GAP-AND-GO VALIDATION ENGINE (2023-2024)")
    print(f"Universe: {len(symbols)} Equities | Causal Next-Bar | Realistic Friction")
    print("=" * 80)

    symbol_data = {}
    for sym in symbols:
        try:
            symbol_data[sym] = load_canonical_data(data_dir, sym, start_date, end_date)
        except Exception as e:
            print(f"Skipping {sym}: {e}")

    # Gather prior close mapping per symbol
    daily_closes = {}
    for sym, df in symbol_data.items():
        d_close = df.groupby("date")["close"].last().to_dict()
        daily_closes[sym] = d_close

    all_dates = sorted(list(set.union(*[set(df["date"].unique()) for df in symbol_data.values()])))
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    nav = initial_nav
    equity_curve = []
    closed_trades = []
    
    # 0.5% NAV risk per trade
    risk_pct = 0.005
    slip_rate = 0.00015
    fee_rate = 0.0002

    for d_idx, d in enumerate(all_dates):
        if d_idx == 0:
            continue
        prev_date = all_dates[d_idx - 1]

        candidates = []
        for sym, df in symbol_data.items():
            if sym in ["SPY", "QQQ"]:
                continue

            prev_close = daily_closes[sym].get(prev_date)
            if not prev_close:
                continue

            sub = df[df["date"] == d]
            if len(sub) < 30:
                continue

            c = sub["close"].values
            h = sub["high"].values
            l = sub["low"].values
            o = sub["open"].values

            # Gap >= 0.6%
            gap_pct = (o[0] - prev_close) / prev_close
            orb_5m_h = np.max(h[:5])

            # Confirmation: 15m candle closes above 5m high
            if gap_pct >= 0.006 and c[15] > orb_5m_h:
                # ATR(14)
                tr = np.maximum(h[:16] - l[:16], np.maximum(np.abs(h[:16] - np.roll(c[:16], 1)), np.abs(l[:16] - np.roll(c[:16], 1))))
                tr[0] = h[0] - l[0]
                atr = np.mean(tr[-14:])

                candidates.append({
                    "symbol": sym,
                    "sector": SECTOR_MAP.get(sym, "OTHER"),
                    "gap_pct": gap_pct,
                    "entry_raw": o[16],
                    "atr": atr,
                    "day_df": sub
                })

        # Rank by largest opening gap
        candidates.sort(key=lambda x: x["gap_pct"], reverse=True)

        # Portfolio Constraints: Max 2 concurrent positions
        admitted = []
        for cand in candidates:
            if len(admitted) >= 2:
                break
            admitted.append(cand)

        for pos in admitted:
            day_df = pos["day_df"]
            c = day_df["close"].values
            h = day_df["high"].values
            l = day_df["low"].values
            t_et = day_df["time_et"].dt.time.values
            n_bars = len(day_df)

            entry_px = pos["entry_raw"] * (1.0 + slip_rate)
            risk = 1.5 * pos["atr"]
            if risk <= 0.05:
                continue

            stop_px = entry_px - risk
            target_px = entry_px + 2.5 * risk

            dollar_risk = nav * risk_pct
            shares = int(dollar_risk / risk)
            if shares < 1:
                continue

            exit_px = None
            hit_reason = "EOD"

