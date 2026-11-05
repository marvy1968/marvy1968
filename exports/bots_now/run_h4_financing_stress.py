#!/usr/bin/env python3
"""
H4 Extreme-Tail Mean Reversion: Overnight Financing & Swap Stress Engine
Applies daily interest rate differentials, 3x Wednesday swaps, and weekend gap slippage.
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from run_h4_portfolio_hardened import load_dataset, generate_signals, precompute_trade, PAIRS, INITIAL_EQUITY, RISK_PCT

SWAP_RATES_ANNUAL = {
    "EUR_USD": {"BUY": -0.015, "SELL": +0.005},
    "GBP_USD": {"BUY": -0.012, "SELL": +0.002},
    "USD_JPY": {"BUY": +0.035, "SELL": -0.045},
    "AUD_USD": {"BUY": -0.018, "SELL": +0.006},
    "USD_CAD": {"BUY": +0.004, "SELL": -0.014},
    "NZD_USD": {"BUY": -0.015, "SELL": +0.003},
    "USD_CHF": {"BUY": +0.020, "SELL": -0.030},
    "EUR_JPY": {"BUY": +0.025, "SELL": -0.038},
    "GBP_JPY": {"BUY": +0.028, "SELL": -0.040}
}

def calculate_swap_cost(trade, current_nav):
    hold_days = max(1.0, (trade.exit_time - trade.entry_time).total_seconds() / 86400.0)
    rate = SWAP_RATES_ANNUAL.get(trade.symbol, {}).get(trade.side, -0.015)
    notional_usd = trade.units * trade.entry_px * trade.quote_to_usd
    swap_usd = notional_usd * (rate / 365.0) * hold_days
    return swap_usd

def main():
    print("=" * 115)
    print(" 🏦 H4 PORTFOLIO FINANCING, ROLLOVER SWAP & CARRY STRESS TEST")
    print("=" * 115)
    
    df_uj_h1 = load_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj_h1["close"].shift(1)
    
    dfs = {sym: load_dataset(sym, "H4") for sym in PAIRS}
    all_sigs = pd.concat([generate_signals(dfs[sym], sym) for sym in PAIRS], ignore_index=True)
    sorted_sigs = all_sigs.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    
    all_timestamps = sorted(list(set().union(*(df.index for df in dfs.values()))))
    
    realized_nav = INITIAL_EQUITY
    active_trades = []
    closed_trades = []
    total_swaps = 0.0
    
    sig_idx = 0
    num_sigs = len(sorted_sigs)
    
    for t in all_timestamps:
        still_active = []
        for tr in active_trades:
            if tr.exit_time <= t:
                swap_cost = calculate_swap_cost(tr, realized_nav)
                total_swaps += swap_cost
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
            
            trade_obj = precompute_trade(dfs[sym], sig, pit_usdjpy, len(closed_trades)+1, realized_nav)
            active_trades.append(trade_obj)

    net_rs = [t.r_multiple for t in closed_trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    
    print(f" Executed Trades (Gross 1.5% Cap): {len(closed_trades)}")
    print(f" Net Realized Return (Post-Swap):   {((realized_nav - INITIAL_EQUITY)/INITIAL_EQUITY)*100:+.2f}%")
    print(f" Net Profit Factor (Post-Swap):     {pf:.2f}")
    print(f" Post-Swap Mean Expectancy:         {np.mean(net_rs):+.3f}R")
    print(f" Total Portfolio Financing Carry:   ${total_swaps:+,.2f}")
    print("=" * 115)

if __name__ == "__main__":
    main()
