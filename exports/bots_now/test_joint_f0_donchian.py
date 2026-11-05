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
from run_h4_filter_sweep import generate_filtered_signals as f0_signals
from test_challenger_donchian import generate_donchian_signals

if __name__ == "__main__":
    # 1. Mandatory Canonical Parity Gate
    verify_canonical_f0_parity()

    print("=" * 85)
    print("BUILDING UNGATED JOINT PORTFOLIO (F0 + DONCHIAN-20)")
    print("=" * 85)

    # 2. Extract both candidate universes through identical path physics
    print("Extracting F0 candidate events...")
    ev_f0 = build_events_from_signals(f0_signals, "F0")
    ev_f0["strategy"] = "F0"
    ev_f0["priority"] = 1  # F0-First slot priority

    print("Extracting Donchian candidate events...")
    ev_don = build_events_from_signals(generate_donchian_signals, "DONCHIAN")
    ev_don["strategy"] = "DONCHIAN"
    ev_don["priority"] = 2  # Donchian claims remaining capacity only

    print(f"Candidates Extracted: F0 = {len(ev_f0)} | Donchian = {len(ev_don)}")

    # 3. Combine with deterministic F0-first priority ordering
    combined_events = pd.concat([ev_f0, ev_don], ignore_index=True)
    combined_events = combined_events.sort_values(
        by=["entry_time", "priority", "pair"]
    ).reset_index(drop=True)

    print(f"Total Unified Ticket Stream: {len(combined_events)} candidate events.")

    # 4. Execute through Canonical Portfolio 3 (3 Slots + Factor Firewall)
    print("\nSimulating Shared-Capital Execution Engine...")
    nav_joint, trades_joint = simulate_h4_portfolio(combined_events, max_positions=3, use_firewall=True)

    # 5. Output Canonical Metrics
    print_portfolio_metrics("PORTFOLIO 3: JOINT F0 + DONCHIAN (UNGATED, F0-FIRST)", nav_joint, trades_joint)

    # 6. Granular Strategy Contribution Breakdown
    print("=" * 85)
    print("CAPITAL & TICKET CONSUMPTION BREAKDOWN")
    print("=" * 85)
    strat_counts = trades_joint["pair"].copy()
    # Map executed trades back to strategy origin via matching entry_time and pair
    trades_with_strat = pd.merge(
        trades_joint,
        combined_events[["entry_time", "pair", "side", "strategy"]].drop_duplicates(subset=["entry_time", "pair", "side"]),
        on=["entry_time", "pair", "side"],
        how="left"
    )

    print("Trades Executed by Sleeve:")
    for strat, group in trades_with_strat.groupby("strategy"):
        w = group[group["r_mult"] > 0]["r_mult"].sum()
        l = abs(group[group["r_mult"] <= 0]["r_mult"].sum())
        pf = w / l if l > 0 else float("inf")
        print(f"  {strat:<10} | Executed: {len(group):<4} | Net Mean R: {group['r_mult'].mean():+6.3f}R | PF: {pf:4.2f} | Total PnL: ${group['pnl_usd'].sum():+10.2f}")

    print("\nYear-by-Year Sleeve Attribution ($):")
    pnl_by_year_strat = trades_with_strat.groupby(["year", "strategy"])["pnl_usd"].sum().unstack().fillna(0.0)
    pnl_by_year_strat["Joint_Total"] = pnl_by_year_strat.sum(axis=1)
    print(pnl_by_year_strat.round(2).to_string())
