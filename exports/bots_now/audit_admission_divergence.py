#!/usr/bin/env python3
import sys
import pandas as pd
import numpy as np

from run_hardened_challenger_framework import build_events_from_signals
from test_gated_f0_donchian import generate_gated_donchian_signals
from test_dual_gated_2plus1 import generate_dual_gated_donchian_signals

print("=" * 80)
print("AUDITING DONCHIAN CANDIDATE SUBSETS (ADX-ONLY vs DUAL-GATED)")
print("=" * 80)

ev_adx = build_events_from_signals(generate_gated_donchian_signals, "DONCHIAN_ADX")
ev_dual = build_events_from_signals(generate_dual_gated_donchian_signals, "DONCHIAN_DUAL")

print(f"Total ADX Candidates:        {len(ev_adx)}")
print(f"Total Dual-Gated Candidates:  {len(ev_dual)}")

# Check subset property: (pair, entry_time, side)
keys_adx = set(zip(ev_adx["pair"], ev_adx["entry_time"], ev_adx["side"]))
keys_dual = set(zip(ev_dual["pair"], ev_dual["entry_time"], ev_dual["side"]))

is_subset = keys_dual.issubset(keys_adx)
print(f"\nIs Dual-Gated a strict subset of ADX-only? >>> {is_subset} <<<")

if not is_subset:
    diff = keys_dual - keys_adx
    print(f"FATAL: Found {len(diff)} trades in Dual-Gated that were NOT in ADX-only!")
    print(list(diff)[:5])
    sys.exit(1)
else:
    print(f"VERIFIED: Exactly {len(keys_adx - keys_dual)} candidates were filtered out by ATR_ratio <= 1.00.")

# Audit Execution Divergence
print("\n" + "=" * 80)
print("TRACING EXECUTION PATH DIVERGENCE")
print("=" * 80)

from test_reserved_slot_2plus1 import simulate_h4_portfolio_reserved
from run_h4_filter_sweep import generate_filtered_signals as f0_signals

ev_f0 = build_events_from_signals(f0_signals, "F0")
ev_f0["strategy"] = "F0"
ev_f0["priority"] = 1

# Run 1: ADX Only
ev_adx["strategy"] = "DONCHIAN_GATED"
ev_adx["priority"] = 2
c1 = pd.concat([ev_f0, ev_adx], ignore_index=True).sort_values(by=["entry_time", "priority", "pair"]).reset_index(drop=True)
nav_1, tr_1 = simulate_h4_portfolio_reserved(c1, 3, True)

# Run 2: Dual Gated
ev_dual["strategy"] = "DONCHIAN_GATED"
ev_dual["priority"] = 2
c2 = pd.concat([ev_f0, ev_dual], ignore_index=True).sort_values(by=["entry_time", "priority", "pair"]).reset_index(drop=True)
nav_2, tr_2 = simulate_h4_portfolio_reserved(c2, 3, True)

tr1_don = tr_1[tr_1["strategy"] == "DONCHIAN_GATED"].reset_index(drop=True)
tr2_don = tr_2[tr_2["strategy"] == "DONCHIAN_GATED"].reset_index(drop=True)

print(f"ADX-Only Executed Donchian Trades:    {len(tr1_don)}")
print(f"Dual-Gated Executed Donchian Trades:  {len(tr2_don)}")

# Find the very first divergence in executed trades
diverged = False
for idx in range(min(len(tr1_don), len(tr2_don))):
    t1 = tr1_don.iloc[idx]
    t2 = tr2_don.iloc[idx]
    if (t1["pair"], t1["entry_time"], t1["side"]) != (t2["pair"], t2["entry_time"], t2["side"]):
        print(f"\nFirst Execution Divergence at trade index {idx}:")
        print(f"  Run 1 (ADX-Only):   {t1['pair']} {t1['side']} entered at {t1['entry_time']} (held {t1['bars_held']} bars, R={t1['r_mult']:+.2f})")
        print(f"  Run 2 (Dual-Gated): {t2['pair']} {t2['side']} entered at {t2['entry_time']} (held {t2['bars_held']} bars, R={t2['r_mult']:+.2f})")
        diverged = True
        break

if not diverged:
    print("Execution sequences remained identical until one ran out of trades.")
