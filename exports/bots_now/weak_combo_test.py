#!/usr/bin/env python3
"""
WEAK-EXPERT ENSEMBLE TEST HARNESS
Tests regime-gated combination of prototypes #1, #2, #4, and #6.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from tournament_engine import (
    load_and_prep_data,
    backtest_expert,
    Position,
)

def signal_weak_combo_regime(df: pd.DataFrame, idx: int, pos: Position, spy_df: pd.DataFrame) -> dict:
    row = df.iloc[idx]
    date = df.index[idx]

    if date not in spy_df.index or idx < 200:
        return {"signal": "HOLD"}

    spy_row = spy_df.loc[date]

    # --- POSITION MANAGEMENT ---
    if pos is not None:
        if pos.side == "BUY":
            # G3-B5 ratchet trail
            candidate = float(row["close"]) - 2.0 * float(row["atr14"])
            if candidate > pos.current_stop:
                pos.current_stop = candidate

            # Connors fast mean-reversion exit
            if row["close"] > row["sma5"]:
                return {"signal": "EXIT", "reason": "COMBO_FAST_TP"}
        return {"signal": "HOLD"}

    # --- ENTRY FILTERS ---

    # Setup 1: Velez + Connors High-Quality Mean Reversion
    # (Above 200 SMA, extreme 2-day dip, tight ATR cushion near 20 EMA)
    if (
        row["close"] > row["sma200"]
        and row["rsi2"] < 10.0
        and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.02
        and row["atr_ratio"] <= 0.055
    ):
        return {
            "signal": "BUY",
            "stop": float(row["low"]) - 0.75 * float(row["atr14"]),
            "target_r": 2.5,
        }

    # Setup 2: Filtered Donchian Breakout
    # (Stage 2 alignment + 30-day Donchian breakout + low volatility compression)
    if (
        row["close"] > row["donch_high_30"]
        and row["close"] > row["sma50"] > row["sma200"]
        and row["atr_ratio"] <= 0.045
    ):
        return {
            "signal": "BUY",
            "stop": float(row["close"]) - 1.5 * float(row["atr14"]),
            "target_r": 3.0,
        }

    # Setup 3: Market-Gated Stage 4 Short
    # (SPY below 50 SMA confirms macro weakness before taking single-stock breakdowns)
    if (
        spy_row["close"] < spy_row["sma50"]
        and row["close"] < row["sma50"] < row["sma200"]
        and row["close"] < row["donch_low_30"]
        and row["vol_surge"] > 1.20
    ):
        return {
            "signal": "SELL",
            "stop": float(row["close"]) + 1.25 * float(row["atr14"]),
            "target_r": 2.0,
        }

    return {"signal": "HOLD"}


def run_weak_combo():
    print("=" * 90)
    print("RUNNING REGIME-GATED WEAK-EXPERT ENSEMBLE TEST (#1, #2, #4, #6)")
    print("=" * 90)

    dfs = load_and_prep_data()

    metrics, trades, equity = backtest_expert(
        "8. Sector Top-Down",  # Reusing the 4-arg signature handler branch in the engine
        signal_weak_combo_regime,
        dfs,
    )

    print("\n--- WEAK COMBO EXPERIMENT METRICS ---")
    print(f"Total Trades:     {metrics['trades']}")
    print(f"Win Rate:         {metrics['win_rate']:.1f}%")
    print(f"Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"Expectancy (R):   {metrics['expectancy_r']:+.3f}R")
    print(f"Median R:         {metrics['median_r']:+.3f}R")
    print(f"Net PnL:          ${metrics['net_pnl']:+,.2f}")
    print(f"Return:           {metrics['return_pct']:+.2f}%")
    print(f"MTM Max DD:       {metrics['max_drawdown']:.2f}%")
    print(f"Gate Status:      {'PASS' if metrics['pass_phase1'] else 'FAIL'}")
    print("=" * 90)


if __name__ == "__main__":
    run_weak_combo()
