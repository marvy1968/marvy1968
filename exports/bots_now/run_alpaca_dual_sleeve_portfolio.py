#!/usr/bin/env python3
"""
Alpaca Dual-Sleeve Production Engine
Sleeve A (50% NAV): Oliver Velez 20/200 EMA Reversal (Fixed 3R Target)
Sleeve B (50% NAV): G3-B5 30-Day Donchian Trend (Uncapped 2.0x ATR Ratchet)
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path

STOCK_DATA_DIR = Path("/home/moonrider1968/bots/stock_data")
symbols = ["AAPL", "NVDA", "MSFT", "AMD", "TSLA", "AMZN", "GOOGL", "META", "XLK", "QQQ", "SPY"]

dfs = {}
for sym in symbols:
    p = STOCK_DATA_DIR / f"{sym}_daily.csv"
    if p.exists():
        d = pd.read_csv(p, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
        d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
        d["sma50"] = d["close"].rolling(50).mean()
        d["sma200"] = d["close"].rolling(200).mean()
        d["donch_high_30"] = d["high"].rolling(30).max().shift(1)
        tr = np.maximum(d["high"] - d["low"], np.maximum(abs(d["high"] - d["close"].shift(1)), abs(d["low"] - d["close"].shift(1))))
        d["atr14"] = tr.rolling(14).mean()
        dfs[sym] = d.dropna()

spy_df = dfs.get("SPY", dfs.get("QQQ"))
dates = spy_df.index[200:]

cash = 100_000.0
positions = {} # {id: {sym, sleeve, entry_px, stop_px, target_px, qty, init_risk}}
trade_log = []
equity_curve = []

for dt in dates:
    spy_row = spy_df.loc[dt]
    macro_bull = spy_row["close"] > spy_row["ema20"] > spy_row["sma50"]
    
    # 1. Process Exits
    for pid in list(positions.keys()):
        pos = positions[pid]
        df = dfs[pos["sym"]]
        if dt not in df.index:
            continue
        row = df.loc[dt]
        
        exit_px = None
        if pos["sleeve"] == "VELEZ":
            if row["low"] <= pos["stop_px"]:
                exit_px = pos["stop_px"]
            elif row["high"] >= pos["target_px"]:
                exit_px = pos["target_px"]
            elif row["close"] < row["ema20"]:
                exit_px = float(row["close"])
        else: # G3-B5 TREND
            if not macro_bull or row["low"] <= pos["stop_px"]:
                exit_px = float(row["open"]) if not macro_bull else pos["stop_px"]
            else:
                cand_stop = float(row["close"]) - (2.0 * float(row["atr14"]))
                if cand_stop > pos["stop_px"]:
                    pos["stop_px"] = cand_stop

        if exit_px is not None:
            pnl = (exit_px - pos["entry_px"]) * pos["qty"]
            cash += (exit_px * pos["qty"])
            r_mult = pnl / (pos["init_risk"] * pos["qty"]) if pos["init_risk"] > 0 else 0
            trade_log.append({"sleeve": pos["sleeve"], "sym": pos["sym"], "pnl": pnl, "r": r_mult, "win": pnl > 0})
            del positions[pid]

    # Current NAV
    mtm_val = sum(positions[p]["qty"] * float(dfs[positions[p]["sym"]].loc[dt, "close"]) for p in positions if dt in dfs[positions[p]["sym"]].index)
    total_nav = cash + mtm_val
    equity_curve.append((dt, total_nav))

    # 2. Process Entries
    sleeve_counts = {"VELEZ": sum(1 for p in positions.values() if p["sleeve"] == "VELEZ"),
                     "TREND": sum(1 for p in positions.values() if p["sleeve"] == "TREND")}

    for sym in symbols:
        if sym in ["SPY", "QQQ"]:
            continue
        df = dfs[sym]
        if dt not in df.index:
            continue
        row = df.loc[dt]

        # Sleeve A: Velez (Max 2 concurrent positions, 0.50% NAV risk)
        if sleeve_counts["VELEZ"] < 2 and row["close"] > row["sma200"] and row["close"] > row["ema20"] and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.015 and row["close"] > row["open"]:
            entry_px = float(row["close"])
            stop_px = float(row["low"]) - (0.5 * float(row["atr14"]))
            init_risk = entry_px - stop_px
            if init_risk > 0:
                qty = int((total_nav * 0.005) / init_risk)
                if qty > 0 and cash >= (qty * entry_px):
                    cash -= (qty * entry_px)
                    pid = f"V_{sym}_{dt.strftime('%Y%m%d')}"
                    positions[pid] = {"sym": sym, "sleeve": "VELEZ", "entry_px": entry_px, "stop_px": stop_px, "target_px": entry_px + (3.0 * init_risk), "qty": qty, "init_risk": init_risk}
                    sleeve_counts["VELEZ"] += 1

        # Sleeve B: G3-B5 Trend (Max 3 concurrent positions, 0.50% NAV risk)
        if macro_bull and sleeve_counts["TREND"] < 3 and row["close"] > row["donch_high_30"]:
            entry_px = float(row["close"])
            stop_px = entry_px - (2.0 * float(row["atr14"]))
            init_risk = entry_px - stop_px
            if init_risk > 0:
                qty = int((total_nav * 0.005) / init_risk)
                if qty > 0 and cash >= (qty * entry_px):
                    cash -= (qty * entry_px)
                    pid = f"T_{sym}_{dt.strftime('%Y%m%d')}"
                    positions[pid] = {"sym": sym, "sleeve": "TREND", "entry_px": entry_px, "stop_px": stop_px, "target_px": None, "qty": qty, "init_risk": init_risk}
                    sleeve_counts["TREND"] += 1

df_t = pd.DataFrame(trade_log)
eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve])
peak = eq.cummax()
max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0

print("=" * 90)
print(" 🏆 COMBINED DUAL-SLEEVE (VELEZ + G3-B5 TREND) PORTFOLIO PERFORMANCE")
print("=" * 90)
print(f"Initial Balance     : $100,000.00")
print(f"Final Portfolio NAV : ${eq.iloc[-1]:,.2f} ({((eq.iloc[-1]/100000.0)-1)*100:+.2f}%)")
print(f"Max Portfolio DD    : {max_dd:.2f}%")
print(f"Total Trades        : {len(df_t)} (Sleeve A Velez: {len(df_t[df_t['sleeve']=='VELEZ'])}, Sleeve B Trend: {len(df_t[df_t['sleeve']=='TREND'])})")
print(f"Portfolio Win Rate  : {(df_t['win'].mean()*100):.2f}%")
print(f"Mean Expectancy     : {df_t['r'].mean():+.3f}R")
print("=" * 90)
