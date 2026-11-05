#!/usr/bin/env python3
"""
H4 Extreme-Tail Mean Reversion: 9-Pair Chronological Portfolio Simulator
Evaluates concurrent risk, currency factor exposure, and position-cap dynamics.
Engine: E4 Core (100 SMA | +/-2.5s Reclaim | +/-3.5s Stop | T1 Centerline Target)
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050  # 0.50% per trade
ENTRY_SLIPPAGE_PIPS = 0.25
EXIT_SLIPPAGE_PIPS = 0.25

SPREAD_PIPS_MAP = {
    "EUR_USD": 1.0, "GBP_USD": 1.4, "USD_JPY": 1.1, "AUD_USD": 1.2,
    "USD_CAD": 1.5, "NZD_USD": 1.6, "USD_CHF": 1.5, "EUR_JPY": 1.6, "GBP_JPY": 2.2
}

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def load_dataset(symbol: str, granularity: str = "H4") -> pd.DataFrame:
    sub = granularity.lower()
    for p in [DATA_DIR / sub / f"{symbol}_{granularity}.csv", DATA_DIR / f"{symbol}_{granularity}.csv", Path(f"/home/moonrider1968/bots/{symbol}_{granularity}.csv")]:
        if p.exists():
            df = pd.read_csv(p)
            time_col = next(c for c in df.columns if c.lower() in ["timestamp", "time", "date"])
            df[time_col] = pd.to_datetime(df[time_col], utc=True)
            return df.sort_values(time_col).set_index(time_col)
    raise FileNotFoundError(f"Missing data for {symbol} ({granularity})")

def generate_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    pip_val = get_pip_value(symbol)
    df["ma100"] = df["close"].rolling(100).mean()
    df["std100"] = df["close"].rolling(100).std()
    df["upper_25s"] = df["ma100"] + (2.5 * df["std100"])
    df["lower_25s"] = df["ma100"] - (2.5 * df["std100"])
    df["upper_35s"] = df["ma100"] + (3.5 * df["std100"])
    df["lower_35s"] = df["ma100"] - (3.5 * df["std100"])
    
    signals = []
    for i in range(105, len(df) - 5):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]
        
        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma100"]:
            stop_dist = cur["close"] - cur["lower_35s"]
            signals.append({
                "timestamp": t, "symbol": symbol, "side": "BUY",
                "stop_pips": max(6.0, stop_dist / pip_val), "target_px": float(cur["ma100"])
            })
        elif prev["close"] > prev["upper_25s"] and cur["close"] < cur["upper_25s"] and cur["close"] > cur["ma100"]:
            stop_dist = cur["upper_35s"] - cur["close"]
            signals.append({
                "timestamp": t, "symbol": symbol, "side": "SELL",
                "stop_pips": max(6.0, stop_dist / pip_val), "target_px": float(cur["ma100"])
            })
    return pd.DataFrame(signals)

def evaluate_trade(df: pd.DataFrame, sig: dict, pit_usdjpy: Optional[pd.Series], current_nav: float) -> dict:
    sym = sig["symbol"]
    side = sig["side"]
    pip_val = get_pip_value(sym)
    spread = SPREAD_PIPS_MAP.get(sym, 1.5)
    half_spread = (spread / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val
    stop_dist = sig["stop_pips"] * pip_val
    
    entry_idx = df.index.get_loc(sig["timestamp"])
    raw_entry = float(df["close"].iloc[entry_idx])
    
    if side == "BUY":
        entry_px = raw_entry + half_spread + entry_slip
        stop_px = entry_px - stop_dist
        target_px = sig["target_px"]
    else:
        entry_px = raw_entry - half_spread - entry_slip
        stop_px = entry_px + stop_dist
        target_px = sig["target_px"]
        
    base, quote = sym.split("_")
    t_entry = sig["timestamp"]
    
    if quote == "USD": quote_to_usd = 1.0
    elif base == "USD": quote_to_usd = 1.0 / entry_px if entry_px > 0 else 1.0
    elif quote == "JPY" and pit_usdjpy is not None:
        uj = pit_usdjpy.asof(t_entry)
        quote_to_usd = 1.0 / uj if pd.notna(uj) and uj > 0 else 1.0 / 150.0
    else: quote_to_usd = 1.0
    
    risk_per_unit = stop_dist * quote_to_usd
    dollar_risk = current_nav * RISK_PCT
    units = dollar_risk / risk_per_unit if risk_per_unit > 0 else 0
    
    # Forward simulation
    exit_px, reason, exit_time, bars = entry_px, "DATASET_END", df.index[-1], len(df) - entry_idx
    for count, idx in enumerate(range(entry_idx + 1, len(df)), start=1):
        row = df.iloc[idx]
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        t = df.index[idx]
        
        if side == "BUY":
            if o <= stop_px: exit_px, reason, exit_time, bars = o - half_spread - exit_slip, "STOP_HIT", t, count; break
            if o >= target_px: exit_px, reason, exit_time, bars = o - half_spread - exit_slip, "TARGET_HIT", t, count; break
            if l <= stop_px: exit_px, reason, exit_time, bars = stop_px - half_spread - exit_slip, "STOP_HIT", t, count; break
            if h >= target_px: exit_px, reason, exit_time, bars = target_px - half_spread - exit_slip, "TARGET_HIT", t, count; break
        else:
            if o >= stop_px: exit_px, reason, exit_time, bars = o + half_spread + exit_slip, "STOP_HIT", t, count; break
            if o <= target_px: exit_px, reason, exit_time, bars = o + half_spread + exit_slip, "TARGET_HIT", t, count; break
            if h >= stop_px: exit_px, reason, exit_time, bars = stop_px + half_spread + exit_slip, "STOP_HIT", t, count; break
            if l <= target_px: exit_px, reason, exit_time, bars = target_px + half_spread + exit_slip, "TARGET_HIT", t, count; break
            
    pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
    net_pnl_usd = pnl * quote_to_usd
    r_mult = net_pnl_usd / dollar_risk if dollar_risk > 0 else 0.0
    
    return {
        "symbol": sym, "side": side, "entry_time": t_entry, "exit_time": exit_time,
        "entry_px": entry_px, "exit_px": exit_px, "net_pnl_usd": net_pnl_usd,
        "r_multiple": r_mult, "exit_reason": reason, "bars": bars, "dollar_risk": dollar_risk
    }

def run_portfolio(all_signals: pd.DataFrame, dfs: Dict[str, pd.DataFrame], pit_usdjpy: pd.Series, max_concurrent: Optional[int] = None) -> dict:
    active_positions = []
    closed_trades = []
    nav = INITIAL_EQUITY
    equity_curve = []
    concurrency_record = defaultdict(int)
    currency_exposure = defaultdict(lambda: defaultdict(int)) # time -> curr -> net_units
    
    # Sort signals strictly chronologically
    sorted_signals = all_signals.sort_values("timestamp").to_dict("records")
    
    for sig in sorted_signals:
        t_sig = sig["timestamp"]
        # 1. Close out expired positions before evaluating new entry
        active_positions = [p for p in active_positions if p["exit_time"] > t_sig]
        
        # 2. Check position limit constraints
        if max_concurrent is not None and len(active_positions) >= max_concurrent:
            continue
            
        # Prevent stacking multiple trades on the same pair concurrently
        if any(p["symbol"] == sig["symbol"] for p in active_positions):
            continue
            
        trade = evaluate_trade(dfs[sig["symbol"]], sig, pit_usdjpy, nav)
        active_positions.append(trade)
        closed_trades.append(trade)
        
    # Chronological equity calculation
    closed_trades.sort(key=lambda x: x["exit_time"])
    cum_nav = INITIAL_EQUITY
    curve = [INITIAL_EQUITY]
    for t in closed_trades:
        cum_nav += t["net_pnl_usd"]
        curve.append(cum_nav)
        
    curve_arr = np.array(curve)
    cummax = np.maximum.accumulate(curve_arr)
    max_dd = float(np.max((cummax - curve_arr) / cummax) * 100.0) if len(curve_arr) > 0 else 0.0
    
    net_rs = [t["r_multiple"] for t in closed_trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    
    return {
        "max_concurrent": max_concurrent or 99,
        "executed_trades": len(closed_trades),
        "total_pnl": cum_nav - INITIAL_EQUITY,
        "final_nav": cum_nav,
        "return_pct": ((cum_nav - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0,
        "net_pf": pf,
        "mean_r": float(np.mean(net_rs)) if net_rs else 0.0,
        "win_rate": len(wins) / len(closed_trades) * 100.0 if closed_trades else 0.0,
        "max_dd_pct": max_dd
    }

def main():
    print("=" * 115)
    print(" 🚀 H4 CHRONOLOGICAL 9-PAIR PORTFOLIO SIMULATION & CONCURRENCY AUDIT")
    print("=" * 115)
    
    df_uj_h1 = load_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj_h1["close"].shift(1)
    
    dfs = {sym: load_dataset(sym, "H4") for sym in PAIRS}
    all_sigs_list = [generate_signals(dfs[sym], sym) for sym in PAIRS]
    all_signals = pd.concat(all_sigs_list, ignore_index=True)
    
    # Concurrency Sweep
    cap_levels = [1, 2, 3, 4, 5, 6, None]
    results = []
    
    for cap in cap_levels:
        res = run_portfolio(all_signals, dfs, pit_usdjpy, max_concurrent=cap)
        results.append(res)
        
    print(f"{'Position Cap':<14} | {'Trades':<8} | {'Win %':<7} | {'Net Mean R':<11} | {'Net PF':<8} | {'Total Return':<13} | {'Max DD %':<9}")
    print("-" * 115)
    for r in results:
        cap_label = "Uncapped" if r["max_concurrent"] == 99 else f"Max {r['max_concurrent']} Pairs"
        print(f"{cap_label:<14} | {r['executed_trades']:<8d} | {r['win_rate']:>5.1f}% | {r['mean_r']:>+8.3f}R   | {r['net_pf']:>6.2f} | {r['return_pct']:>+10.2f}% | {r['max_dd_pct']:>7.2f}%")
    print("=" * 115)

if __name__ == "__main__":
    main()
