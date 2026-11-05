#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from run_hardened_challenger_framework import (
    verify_canonical_f0_parity,
    build_events_from_signals,
    print_portfolio_metrics
)
import run_f0_hardened_portfolio_v2 as engine
from run_h4_filter_sweep import generate_filtered_signals as f0_signals
from test_gated_f0_donchian import generate_gated_donchian_signals

INITIAL_NAV = engine.INITIAL_NAV
RISK_PCT = engine.RISK_PCT
MAX_POSITIONS = engine.MAX_POSITIONS
MAX_FACTOR_EXPOSURE = engine.MAX_FACTOR_EXPOSURE
dfs = engine.dfs
all_times = engine.all_times
time_to_idx = engine.time_to_idx
get_quote_to_usd_rate = engine.get_quote_to_usd_rate

def simulate_h4_portfolio_reserved(events: pd.DataFrame, max_positions: int = 3, use_firewall: bool = True):
    """
    Certified H4 Simulation Loop with 1-Line Admission Variant:
    Donchian capped at <= 1 concurrent slot. F0 can use up to all 3.
    """
    cash = INITIAL_NAV
    active_positions = {}  # pid -> dict
    closed_trades = []
    equity_curve = []
    pid_counter = 0

    entry_map = {}
    for _, row in events.iterrows():
        entry_map.setdefault(row["entry_time"], []).append(row)

    for current_time in all_times:
        # A. Process Exits
        active_pids = list(active_positions.keys())
        for pid in active_pids:
            pos = active_positions[pid]
            if current_time >= pos["exit_time"]:
                quote_to_usd = get_quote_to_usd_rate(pos["pair"], pos["exit_time"])
                pnl_per_unit = (pos["exit_px"] - pos["entry_px"]) if pos["side"] == "BUY" else (pos["entry_px"] - pos["exit_px"])
                pnl_usd = pnl_per_unit * pos["units"] * quote_to_usd
                cash += pos["margin"] + pnl_usd

                closed_trades.append({
                    "pid": pid,
                    "pair": pos["pair"],
                    "side": pos["side"],
                    "strategy": pos["strategy"],
                    "entry_time": pos["entry_time"],
                    "exit_time": pos["exit_time"],
                    "r_mult": pos["r_mult"],
                    "pnl_usd": pnl_usd,
                    "bars_held": pos["bars_held"],
                    "year": pos["entry_time"].year
                })
                del active_positions[pid]

        # B. Mark-to-Market Valuation
        unrealized_usd = 0.0
        active_margin = 0.0
        curr_factors = {}

        for pid, pos in active_positions.items():
            pair = pos["pair"]
            active_margin += pos["margin"]
            if current_time in time_to_idx[pair]:
                c_idx = time_to_idx[pair][current_time]
                cur_close = float(dfs[pair]["close"].iloc[c_idx])
                quote_to_usd = get_quote_to_usd_rate(pair, current_time)
                pnl_per_unit = (cur_close - pos["entry_px"]) if pos["side"] == "BUY" else (pos["entry_px"] - cur_close)
                unrealized_usd += pnl_per_unit * pos["units"] * quote_to_usd

            base, quote = pair.split("_")
            d = 1 if pos["side"] == "BUY" else -1
            curr_factors[base] = curr_factors.get(base, 0) + d
            curr_factors[quote] = curr_factors.get(quote, 0) - d

        mtm_nav = cash + active_margin + unrealized_usd
        equity_curve.append({"time": current_time, "mtm_nav": mtm_nav, "open_pos": len(active_positions)})

        # C. Screen & Size New Entries
        if current_time in entry_map:
            for cand in entry_map[current_time]:
                if len(active_positions) >= max_positions:
                    break

                # 2+1 HYPOTHESIS GATE: Donchian capped at 1 concurrent slot
                if cand.get("strategy") == "DONCHIAN_GATED":
                    active_donchian = sum(1 for p in active_positions.values() if p.get("strategy") == "DONCHIAN_GATED")
                    if active_donchian >= 1:
                        continue

                pair = cand["pair"]
                if any(p["pair"] == pair for p in active_positions.values()):
                    continue

                base, quote = pair.split("_")
                d = 1 if cand["side"] == "BUY" else -1

                if use_firewall:
                    if abs(curr_factors.get(base, 0) + d) > MAX_FACTOR_EXPOSURE:
                        continue
                    if abs(curr_factors.get(quote, 0) - d) > MAX_FACTOR_EXPOSURE:
                        continue

                quote_to_usd = get_quote_to_usd_rate(pair, current_time)
                target_risk_usd = mtm_nav * RISK_PCT
                risk_per_unit_usd = cand["stop_dist_quote"] * quote_to_usd

                if risk_per_unit_usd <= 0:
                    continue

                units = target_risk_usd / risk_per_unit_usd
                margin_req = (units * cand["entry_px"] * quote_to_usd) * 0.0333  # 30:1 leverage

                if cash >= margin_req and units > 0:
                    cash -= margin_req
                    pid_counter += 1
                    active_positions[pid_counter] = {
                        "pair": pair,
                        "side": cand["side"],
                        "strategy": cand.get("strategy", "UNKNOWN"),
                        "entry_time": cand["entry_time"],
                        "exit_time": cand["exit_time"],
                        "entry_px": cand["entry_px"],
                        "exit_px": cand["exit_px"],
                        "units": units,
                        "margin": margin_req,
                        "r_mult": cand["r_mult"],
                        "bars_held": cand["bars_held"]
                    }
                    curr_factors[base] = curr_factors.get(base, 0) + d
                    curr_factors[quote] = curr_factors.get(quote, 0) - d

    nav_df = pd.DataFrame(equity_curve).set_index("time")
    nav_df["peak"] = nav_df["mtm_nav"].cummax()
    nav_df["dd_pct"] = (nav_df["mtm_nav"] - nav_df["peak"]) / nav_df["peak"] * 100.0
    trades_df = pd.DataFrame(closed_trades)
    return nav_df, trades_df

if __name__ == "__main__":
    verify_canonical_f0_parity()

    print("=" * 85)
    print("SIMULATING 2+1 CAPACITY ALLOCATION (F0 <= 3, DONCHIAN <= 1)")
    print("=" * 85)

    ev_f0 = build_events_from_signals(f0_signals, "F0")
    ev_f0["strategy"] = "F0"
    ev_f0["priority"] = 1

    ev_don = build_events_from_signals(generate_gated_donchian_signals, "DONCHIAN_GATED")
    ev_don["strategy"] = "DONCHIAN_GATED"
    ev_don["priority"] = 2

    combined_events = pd.concat([ev_f0, ev_don], ignore_index=True)
    combined_events = combined_events.sort_values(
        by=["entry_time", "priority", "pair"]
    ).reset_index(drop=True)

    nav_2p1, trades_2p1 = simulate_h4_portfolio_reserved(combined_events, max_positions=3, use_firewall=True)
    print_portfolio_metrics("PORTFOLIO 3: 2+1 RESERVED CAPACITY (F0 <= 3, DONCHIAN <= 1)", nav_2p1, trades_2p1)

    print("\nTrades Executed by Sleeve:")
    for strat, group in trades_2p1.groupby("strategy"):
        w = group[group["r_mult"] > 0]["r_mult"].sum()
        l = abs(group[group["r_mult"] <= 0]["r_mult"].sum())
        pf = w / l if l > 0 else float("inf")
        print(f"  {strat:<15} | Executed: {len(group):<4} | Net Mean R: {group['r_mult'].mean():+6.3f}R | PF: {pf:4.2f} | Total PnL: ${group['pnl_usd'].sum():+10.2f}")

    print("\nYear-by-Year Sleeve Attribution ($):")
    pnl_by_year = trades_2p1.groupby(["year", "strategy"])["pnl_usd"].sum().unstack().fillna(0.0)
    pnl_by_year["Joint_Total"] = pnl_by_year.sum(axis=1)
    print(pnl_by_year.round(2).to_string())
