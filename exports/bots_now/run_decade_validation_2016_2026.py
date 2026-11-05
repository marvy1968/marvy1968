#!/usr/bin/env python3
import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path

import run_f0_hardened_portfolio_v2 as engine
from run_hardened_challenger_framework import build_events_from_signals, print_portfolio_metrics
from run_h4_filter_sweep import generate_filtered_signals as f0_signals
from test_gated_f0_donchian import generate_gated_donchian_signals
from test_reserved_slot_2plus1 import simulate_h4_portfolio_reserved

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

print("=" * 85)
print("PHASE 3: DECADE BENCHMARK (2016 - 2026) — 9 CANONICAL PAIRS")
print("=" * 85)

# Load complete extended history into engine structures
ext_dfs = {}
for pair in PAIRS:
    p = EXTENDED_DIR / f"{pair}_H4.csv"
    df = pd.read_csv(p)
    time_col = "timestamp" if "timestamp" in df.columns else "time"
    df["time"] = pd.to_datetime(df[time_col], utc=True)
    df = df.sort_values("time").reset_index(drop=True).set_index("time")
    ext_dfs[pair] = df

engine.dfs = ext_dfs
all_times_set = set()
for df in ext_dfs.values():
    all_times_set.update(df.index)
engine.all_times = sorted(list(all_times_set))
engine.time_to_idx = {
    pair: {t: i for i, t in enumerate(ext_dfs[pair].index)}
    for pair in PAIRS
}

print(f"Historical Horizon: {engine.all_times[0]} -> {engine.all_times[-1]}")
print(f"Total Unique H4 Evaluation Bars: {len(engine.all_times)}")

# 1. Extract Signals across the full 10-year period
print("\nExtracting F0 candidate events (2016-2026)...")
ev_f0 = build_events_from_signals(f0_signals, "F0")
ev_f0["strategy"] = "F0"
ev_f0["priority"] = 1
print(f"F0 Total Candidates: {len(ev_f0)}")

print("\nExtracting ADX>=25 Gated Donchian candidate events (2016-2026)...")
ev_don = build_events_from_signals(generate_gated_donchian_signals, "DONCHIAN_GATED")
ev_don["strategy"] = "DONCHIAN_GATED"
ev_don["priority"] = 2
print(f"Donchian Gated Total Candidates: {len(ev_don)}")

# -------------------------------------------------------------
# RUN CONTENDER A: CANONICAL F0 CONTROL (10 YEARS)
# -------------------------------------------------------------
print("\n" + "=" * 85)
print("SIMULATING CONTENDER A: CANONICAL F0 CONTROL (2016 - 2026)")
print("=" * 85)
nav_f0, trades_f0 = engine.simulate_h4_portfolio(ev_f0, max_positions=3, use_firewall=True)
print_portfolio_metrics("CONTENDER A: CANONICAL F0 CONTROL (10 YEARS)", nav_f0, trades_f0)

# -------------------------------------------------------------
# RUN CONTENDER B: FINALIST C1 (2+1 ARCHITECTURE, 10 YEARS)
# -------------------------------------------------------------
print("\n" + "=" * 85)
print("SIMULATING CONTENDER B: FINALIST C1 (F0 <= 3, DONCHIAN <= 1) (2016 - 2026)")
print("=" * 85)

combined_events = pd.concat([ev_f0, ev_don], ignore_index=True)
combined_events = combined_events.sort_values(
    by=["entry_time", "priority", "pair"]
).reset_index(drop=True)

nav_c1, trades_c1 = simulate_h4_portfolio_reserved(combined_events, max_positions=3, use_firewall=True)
print_portfolio_metrics("CONTENDER B: FINALIST C1 (2+1 ARCHITECTURE, 10 YEARS)", nav_c1, trades_c1)

print("\nFinalist C1 — Trades Executed by Sleeve (10 Years):")
for strat, group in trades_c1.groupby("strategy"):
    w = group[group["r_mult"] > 0]["r_mult"].sum()
    l = abs(group[group["r_mult"] <= 0]["r_mult"].sum())
    pf = w / l if l > 0 else float("inf")
    print(f"  {strat:<15} | Executed: {len(group):<4} | Net Mean R: {group['r_mult'].mean():+6.3f}R | PF: {pf:4.2f} | Total PnL: ${group['pnl_usd'].sum():+10.2f}")

print("\nFinalist C1 — Year-by-Year Sleeve Attribution ($):")
pnl_by_year = trades_c1.groupby(["year", "strategy"])["pnl_usd"].sum().unstack().fillna(0.0)
pnl_by_year["Joint_Total"] = pnl_by_year.sum(axis=1)
print(pnl_by_year.round(2).to_string())

# Comparative Summary
print("\n" + "=" * 85)
print("DECADE VALIDATION SUMMARY (2016 - 2026)")
print("=" * 85)
cagr_f0 = ((nav_f0['mtm_nav'].iloc[-1] / 100000.0) ** (1.0 / 10.68) - 1.0) * 100.0
dd_f0 = nav_f0['dd_pct'].min()
calmar_f0 = abs(cagr_f0 / dd_f0) if dd_f0 < 0 else float('nan')

cagr_c1 = ((nav_c1['mtm_nav'].iloc[-1] / 100000.0) ** (1.0 / 10.68) - 1.0) * 100.0
dd_c1 = nav_c1['dd_pct'].min()
calmar_c1 = abs(cagr_c1 / dd_c1) if dd_c1 < 0 else float('nan')

print(f"{'Metric':<25} | {'Contender A (F0 Control)':<25} | {'Contender B (Finalist C1)':<25}")
print("-" * 80)
print(f"{'Total Trades Executed':<25} | {len(trades_f0):<25} | {len(trades_c1):<25}")
print(f"{'Compounded CAGR':<25} | {cagr_f0:>+7.2f}%{' '*17} | {cagr_c1:>+7.2f}%")
print(f"{'Max True MTM Drawdown':<25} | {dd_f0:>7.2f}%{' '*17} | {dd_c1:>7.2f}%")
print(f"{'Calmar Ratio':<25} | {calmar_f0:>7.2f}{' '*18} | {calmar_c1:>7.2f}")
print(f"{'Final NAV':<25} | ${nav_f0['mtm_nav'].iloc[-1]:>12,.2f}{' '*11} | ${nav_c1['mtm_nav'].iloc[-1]:>12,.2f}")
