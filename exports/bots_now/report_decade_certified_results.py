#!/usr/bin/env python3
import sys
import pandas as pd
import numpy as np
from pathlib import Path

import run_f0_hardened_portfolio_v2 as engine
import run_hardened_challenger_framework as framework
from run_hardened_challenger_framework import build_events_from_signals, print_portfolio_metrics
from run_h4_filter_sweep import generate_filtered_signals as f0_signals
from test_gated_f0_donchian import generate_gated_donchian_signals
import test_reserved_slot_2plus1 as mod_c1

EXTENDED_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

# 1. Load extended data and patch all three modules
ext_dfs = {}
for pair in PAIRS:
    p = EXTENDED_DIR / f"{pair}_H4.csv"
    df = pd.read_csv(p)
    tcol = "timestamp" if "timestamp" in df.columns else "time"
    df["time"] = pd.to_datetime(df[tcol], utc=True)
    df = df.sort_values("time").reset_index(drop=True).set_index("time")
    ext_dfs[pair] = df

engine.dfs = ext_dfs
framework.dfs = ext_dfs
mod_c1.dfs = ext_dfs

all_times_sorted = sorted(list(set().union(*(df.index for df in ext_dfs.values()))))
engine.all_times = all_times_sorted
framework.all_times = all_times_sorted
mod_c1.all_times = all_times_sorted

time_to_idx_dict = {pair: {t: i for i, t in enumerate(ext_dfs[pair].index)} for pair in PAIRS}
engine.time_to_idx = time_to_idx_dict
framework.time_to_idx = time_to_idx_dict
mod_c1.time_to_idx = time_to_idx_dict

# 2. Extract Raw Events
ev_f0 = build_events_from_signals(f0_signals, "F0")
ev_f0["strategy"] = "F0"
ev_f0["priority"] = 1

ev_don = build_events_from_signals(generate_gated_donchian_signals, "DONCHIAN_GATED")
ev_don["strategy"] = "DONCHIAN_GATED"
ev_don["priority"] = 2

combined = pd.concat([ev_f0, ev_don], ignore_index=True).sort_values(
    by=["entry_time", "priority", "pair"]
).reset_index(drop=True)

# 3. Simulate Contender A (Canonical F0 Control)
print("\n" + "=" * 90)
print("CONTENDER A: CANONICAL F0 CONTROL (2016 - 2026)")
print("=" * 90)
nav_f0, trades_f0 = engine.simulate_h4_portfolio(ev_f0, max_positions=3, use_firewall=True)
print_portfolio_metrics("CONTENDER A: F0 CONTROL (10 YEARS)", nav_f0, trades_f0)

# 4. Simulate Contender B (Finalist C1: 2+1 Architecture)
print("\n" + "=" * 90)
print("CONTENDER B: FINALIST C1 (F0 <= 3, DONCHIAN <= 1) (2016 - 2026)")
print("=" * 90)
nav_c1, trades_c1 = mod_c1.simulate_h4_portfolio_reserved(combined, max_positions=3, use_firewall=True)
print_portfolio_metrics("CONTENDER B: FINALIST C1 (10 YEARS)", nav_c1, trades_c1)

trades_c1["entry_time"] = pd.to_datetime(trades_c1["entry_time"], utc=True)
trades_c1["year"] = trades_c1["entry_time"].dt.year

print("\nFinalist C1 — Sleeve Summary (10.67 Years):")
for strat, group in trades_c1.groupby("strategy"):
    w = group[group["r_mult"] > 0]["r_mult"].sum()
    l = abs(group[group["r_mult"] <= 0]["r_mult"].sum())
    pf = w / l if l > 0 else float("inf")
    print(f"  {strat:<15} | Executed: {len(group):<5} | Net Mean R: {group['r_mult'].mean():+6.3f}R | PF: {pf:4.2f} | Total PnL: ${group['pnl_usd'].sum():+10.2f}")

print("\nFinalist C1 — Year-by-Year Dollar PnL ($):")
pnl_by_year = trades_c1.groupby(["year", "strategy"])["pnl_usd"].sum().unstack().fillna(0.0)
pnl_by_year["Joint_Total"] = pnl_by_year.sum(axis=1)
print(pnl_by_year.round(2).to_string())

# Comparative Summary
years_span = (engine.all_times[-1] - engine.all_times[0]).days / 365.25
cagr_f0 = ((nav_f0['mtm_nav'].iloc[-1] / 100000.0) ** (1.0 / years_span) - 1.0) * 100.0
dd_f0 = nav_f0['dd_pct'].min()
calmar_f0 = abs(cagr_f0 / dd_f0) if dd_f0 < 0 else float('nan')

cagr_c1 = ((nav_c1['mtm_nav'].iloc[-1] / 100000.0) ** (1.0 / years_span) - 1.0) * 100.0
dd_c1 = nav_c1['dd_pct'].min()
calmar_c1 = abs(cagr_c1 / dd_c1) if dd_c1 < 0 else float('nan')

print("\n" + "=" * 90)
print(f"DECADE VALIDATION SUMMARY ({engine.all_times[0].year} - {engine.all_times[-1].year}) — {years_span:.2f} YEARS")
print("=" * 90)
print(f"{'Metric':<28} | {'Contender A (F0 Control)':<24} | {'Contender B (Finalist C1)':<24}")
print("-" * 84)
print(f"{'Total Trades Executed':<28} | {len(trades_f0):<24} | {len(trades_c1):<24}")
print(f"{'Win Rate':<28} | {((trades_f0['r_mult'] > 0).mean()*100):>6.1f}%{' '*17} | {((trades_c1['r_mult'] > 0).mean()*100):>6.1f}%")
print(f"{'Net Mean R':<28} | {trades_f0['r_mult'].mean():>+6.3f}R{' '*17} | {trades_c1['r_mult'].mean():>+6.3f}R")
print(f"{'Profit Factor':<28} | {(trades_f0[trades_f0['r_mult']>0]['r_mult'].sum()/abs(trades_f0[trades_f0['r_mult']<=0]['r_mult'].sum())):>6.2f}{' '*18} | {(trades_c1[trades_c1['r_mult']>0]['r_mult'].sum()/abs(trades_c1[trades_c1['r_mult']<=0]['r_mult'].sum())):>6.2f}")
print(f"{'Compounded CAGR':<28} | {cagr_f0:>+6.2f}%{' '*17} | {cagr_c1:>+6.2f}%")
print(f"{'Max True MTM Drawdown':<28} | {dd_f0:>6.2f}%{' '*17} | {dd_c1:>6.2f}%")
print(f"{'Calmar Ratio':<28} | {calmar_f0:>6.2f}{' '*18} | {calmar_c1:>6.2f}")
print(f"{'Final NAV ($100k start)':<28} | ${nav_f0['mtm_nav'].iloc[-1]:>11,.2f}{' '*12} | ${nav_c1['mtm_nav'].iloc[-1]:>11,.2f}")
