#!/usr/bin/env python3
"""
H4 Extreme-Tail Mean Reversion: Monte Carlo Permutation Engine
Extracts the exact 220 post-swap trades (Gross 1.5% Cap) and runs
10,000 reshuffled equity paths with dynamic 0.50% NAV compounding.
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from run_h4_financing_stress import (
    load_dataset, generate_signals, precompute_trade, calculate_swap_cost,
    PAIRS, INITIAL_EQUITY, RISK_PCT
)

def extract_post_swap_trades():
    df_uj_h1 = load_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj_h1["close"].shift(1)
    dfs = {sym: load_dataset(sym, "H4") for sym in PAIRS}
    all_sigs = pd.concat([generate_signals(dfs[sym], sym) for sym in PAIRS], ignore_index=True)
    sorted_sigs = all_sigs.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    all_timestamps = sorted(list(set().union(*(df.index for df in dfs.values()))))
    
    realized_nav = INITIAL_EQUITY
    active_trades = []
    closed_trades = []
    sig_idx = 0
    num_sigs = len(sorted_sigs)
    
    for t in all_timestamps:
        still_active = []
        for tr in active_trades:
            if tr.exit_time <= t:
                swap_cost = calculate_swap_cost(tr, realized_nav)
                tr.net_pnl_usd += swap_cost
                tr.r_multiple = tr.net_pnl_usd / tr.dollar_risk
                realized_nav += tr.net_pnl_usd
                closed_trades.append(tr)
            else:
                still_active.append(tr)
        active_trades = still_active
        
        incoming = []
        while sig_idx < num_sigs and sorted_sigs[sig_idx]["timestamp"] == t:
            incoming.append(sorted_sigs[sig_idx])
            sig_idx += 1
            
        for sig in incoming:
            sym = sig["symbol"]
            if any(p.symbol == sym for p in active_trades):
                continue
            current_risk = sum(p.dollar_risk for p in active_trades)
            if (current_risk + realized_nav * RISK_PCT) > (realized_nav * 0.015 + 1e-6):
                continue
            trade_obj = precompute_trade(dfs[sym], sig, pit_usdjpy, len(closed_trades) + 1, realized_nav)
            active_trades.append(trade_obj)
            
    return [t.r_multiple for t in closed_trades]

def run_monte_carlo(r_multiples: list, num_sims: int = 10_000, seed: int = 42):
    np.random.seed(seed)
    r_arr = np.array(r_multiples)
    n_trades = len(r_arr)
    
    max_drawdowns = np.empty(num_sims)
    final_returns = np.empty(num_sims)
    max_loss_streaks = np.empty(num_sims, dtype=int)
    
    for i in range(num_sims):
        perm_rs = np.random.permutation(r_arr)
        
        # Calculate dynamic compounding NAV curve: NAV_t = NAV_{t-1} * (1 + 0.005 * R)
        growth_factors = 1.0 + (RISK_PCT * perm_rs)
        equity_curve = INITIAL_EQUITY * np.cumprod(growth_factors)
        equity_curve = np.insert(equity_curve, 0, INITIAL_EQUITY)
        
        # Max Drawdown
        cummax = np.maximum.accumulate(equity_curve)
        dd = (cummax - equity_curve) / cummax
        max_drawdowns[i] = np.max(dd) * 100.0
        final_returns[i] = ((equity_curve[-1] - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0
        
        # Max Consecutive Loss Streak
        is_loss = (perm_rs < 0).astype(int)
        streak, max_streak = 0, 0
        for loss in is_loss:
            if loss:
                streak += 1
                if streak > max_streak:
                    max_streak = streak
            else:
                streak = 0
        max_loss_streaks[i] = max_streak
        
    return max_drawdowns, final_returns, max_loss_streaks

def main():
    print("=" * 115)
    print(" 🎲 MONTE CARLO PERMUTATION TEST: 220 POST-SWAP TRADES (10,000 RUNS)")
    print("=" * 115)
    
    r_multiples = extract_post_swap_trades()
    print(f" Extracted Trade Count: {len(r_multiples)}")
    print(f" Empirical Net Mean R:  {np.mean(r_multiples):+.3f}R")
    print(f" Empirical Win Rate:    {(np.sum(np.array(r_multiples) > 0) / len(r_multiples)) * 100:.1f}%")
    print("-" * 115)
    
    drawdowns, returns, streaks = run_monte_carlo(r_multiples, num_sims=10_000)
    
    pcts = [50, 75, 90, 95, 99, 99.9]
    print(" 📊 MAXIMUM DRAWDOWN DISTRIBUTION (% OF PEAK EQUITY):")
    print(f"   • Median (50th %ile):     {np.percentile(drawdowns, 50):>6.2f}%")
    print(f"   • 75th Percentile:        {np.percentile(drawdowns, 75):>6.2f}%")
    print(f"   • 90th Percentile:        {np.percentile(drawdowns, 90):>6.2f}%")
    print(f"   • 95th Percentile:        {np.percentile(drawdowns, 95):>6.2f}%  <-- Institutional 95% VaR")
    print(f"   • 99th Percentile:        {np.percentile(drawdowns, 99):>6.2f}%  <-- Extreme Stress Tail")
    print(f"   • Max Simulated DD:       {np.max(drawdowns):>6.2f}%")
    print("-" * 115)
    
    print(" 📉 CONSECUTIVE LOSS STREAK DISTRIBUTION:")
    print(f"   • Median Loss Streak:     {int(np.percentile(streaks, 50)):>2d} trades")
    print(f"   • 95th %ile Loss Streak:  {int(np.percentile(streaks, 95)):>2d} trades")
    print(f"   • 99th %ile Loss Streak:  {int(np.percentile(streaks, 99)):>2d} trades")
    print(f"   • Worst-Case Loss Streak: {np.max(streaks):>2d} trades")
    print("-" * 115)
    
    print(" 📈 FINAL RETURN PROFILE (AFTER 220 TRADES):")
    print(f"   • 5th %ile Return:        {np.percentile(returns, 5):>+6.2f}%")
    print(f"   • Median Return:          {np.percentile(returns, 50):>+6.2f}%")
    print(f"   • 95th %ile Return:       {np.percentile(returns, 95):>+6.2f}%")
    print(f"   • Probability of Profit:  {(np.sum(returns > 0) / len(returns)) * 100:>5.1f}%")
    print("=" * 115)

if __name__ == "__main__":
    main()
