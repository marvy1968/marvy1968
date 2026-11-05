#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from run_hardened_challenger_framework import (
    verify_canonical_f0_parity,
    build_events_from_signals,
    simulate_h4_portfolio,
    print_portfolio_metrics
)
from run_h4_filter_sweep import generate_filtered_signals as f0_signals, get_pip_value

def generate_gated_donchian_signals(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    d = df.copy()
    pip_val = get_pip_value(sym)
    
    # 1. Donchian Channels & 200 SMA
    d["donchian_high_20"] = d["high"].rolling(20).max().shift(1)
    d["donchian_low_20"] = d["low"].rolling(20).min().shift(1)
    d["donchian_mid_10"] = ((d["high"].rolling(10).max() + d["low"].rolling(10).min()) / 2.0).shift(1)
    d["sma200"] = d["close"].rolling(200).mean()
    
    # 2. ATR 14
    prev_c = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev_c).abs(), (d["low"] - prev_c).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean()

    # 3. Directional Movement & ADX 14
    up_move = d["high"] - d["high"].shift(1)
    down_move = d["low"].shift(1) - d["low"]
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    plus_di = 100.0 * (pd.Series(plus_dm, index=d.index).rolling(14).mean() / d["atr14"])
    minus_di = 100.0 * (pd.Series(minus_dm, index=d.index).rolling(14).mean() / d["atr14"])
    
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    d["adx14"] = dx.rolling(14).mean()

    signals = []
    for i in range(200, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]
        
        c = float(row["close"])
        atr = float(row["atr14"])
        adx = float(row["adx14"])
        
        if pd.isna(atr) or atr <= 0 or pd.isna(adx):
            continue
            
        sma = float(row["sma200"])
        
        # PREDECLARED REGIME GATE: ADX >= 25 required to permit breakout signals
        if adx < 25.0:
            continue

        # Bullish Breakout
        if prev["close"] <= prev["donchian_high_20"] and c > row["donchian_high_20"]:
            if c > sma:
                stop_dist = max(c - float(row["donchian_mid_10"]), 1.5 * atr)
                stop_pips = stop_dist / pip_val
                target_px = c + (3.0 * atr)
                signals.append({
                    "timestamp": d.index[i],
                    "side": "BUY",
                    "stop_pips": stop_pips,
                    "target_px": target_px,
                    "pass_filter": True
                })

        # Bearish Breakdown
        elif prev["close"] >= prev["donchian_low_20"] and c < row["donchian_low_20"]:
            if c < sma:
                stop_dist = max(float(row["donchian_mid_10"]) - c, 1.5 * atr)
                stop_pips = stop_dist / pip_val
                target_px = c - (3.0 * atr)
                signals.append({
                    "timestamp": d.index[i],
                    "side": "SELL",
                    "stop_pips": stop_pips,
                    "target_px": target_px,
                    "pass_filter": True
                })

    return pd.DataFrame(signals)

if __name__ == "__main__":
    verify_canonical_f0_parity()

    print("=" * 85)
    print("SIMULATING REGIME-GATED JOINT PORTFOLIO (F0 + ADX>=25 DONCHIAN)")
    print("=" * 85)

    ev_f0 = build_events_from_signals(f0_signals, "F0")
    ev_f0["strategy"] = "F0"
    ev_f0["priority"] = 1

    ev_don = build_events_from_signals(generate_gated_donchian_signals, "DONCHIAN_GATED")
    ev_don["strategy"] = "DONCHIAN_GATED"
    ev_don["priority"] = 2

    print(f"Candidates: F0 = {len(ev_f0)} | ADX-Gated Donchian = {len(ev_don)}")

    combined_events = pd.concat([ev_f0, ev_don], ignore_index=True)
    combined_events = combined_events.sort_values(
        by=["entry_time", "priority", "pair"]
    ).reset_index(drop=True)

    nav_gated, trades_gated = simulate_h4_portfolio(combined_events, max_positions=3, use_firewall=True)
    print_portfolio_metrics("PORTFOLIO 3: GATED JOINT (F0 + ADX>=25 DONCHIAN)", nav_gated, trades_gated)

    trades_with_strat = pd.merge(
        trades_gated,
        combined_events[["entry_time", "pair", "side", "strategy"]].drop_duplicates(subset=["entry_time", "pair", "side"]),
        on=["entry_time", "pair", "side"],
        how="left"
    )

    print("\nTrades Executed by Sleeve:")
    for strat, group in trades_with_strat.groupby("strategy"):
        w = group[group["r_mult"] > 0]["r_mult"].sum()
        l = abs(group[group["r_mult"] <= 0]["r_mult"].sum())
        pf = w / l if l > 0 else float("inf")
        print(f"  {strat:<15} | Executed: {len(group):<4} | Net Mean R: {group['r_mult'].mean():+6.3f}R | PF: {pf:4.2f} | Total PnL: ${group['pnl_usd'].sum():+10.2f}")

    print("\nYear-by-Year Sleeve Attribution ($):")
    pnl_by_year = trades_with_strat.groupby(["year", "strategy"])["pnl_usd"].sum().unstack().fillna(0.0)
    pnl_by_year["Joint_Total"] = pnl_by_year.sum(axis=1)
    print(pnl_by_year.round(2).to_string())
