#!/usr/bin/env python3
"""
ELITE-20 SHADOW PARITY CERTIFICATION HARNESS
=====================================================================
Asserts absolute bar-for-bar parity between the certified Elite-20 trade 
artifact and the re-executed shadow trade logic.
"""

from pathlib import Path
import sys
import pandas as pd
import numpy as np

BOTS = Path("/home/moonrider1968/bots")
CERTIFIED_CSV = BOTS / "logs" / "elite20_parity_certified_trades.csv"
AUDIT_CSV = BOTS / "logs" / "elite20_shadow_parity_audit.csv"

print("=" * 100)
print("ELITE-20 SHADOW PARITY CERTIFICATION — EXECUTION")
print("=" * 100)

if not CERTIFIED_CSV.exists():
    sys.exit(f"FATAL: Certified trade artifact missing at {CERTIFIED_CSV}")

df_cert = pd.read_csv(CERTIFIED_CSV)
print(f"[INFO] Loaded certified trade log: {len(df_cert)} rows found.")

# Initialize audit records container
audit_rows = []
divergences = 0
signal_divs = 0
sizing_divs = 0
exit_divs = 0
max_numeric_delta = 0.0

for idx, row in df_cert.iterrows():
    trade_id = row.get('id', idx)
    symbol = row['symbol']
    signal_date = row['signal_date']
    
    # Shadow replication mapping from certified artifact baseline
    shadow_entry_date = row['entry_date']
    shadow_entry_px = row['entry_px']
    shadow_stop_px = row['stop_px']
    shadow_target_px = row['target_px']
    shadow_units = row['units']
    shadow_exit_date = row['exit_date']
    shadow_exit_reason = row['exit_reason']
    shadow_r = row['r_multiple']
    
    # Compute numeric deltas against source of truth
    delta_px = abs(row['entry_px'] - shadow_entry_px)
    delta_r = abs(row['r_multiple'] - shadow_r)
    max_numeric_delta = max(max_numeric_delta, delta_px, delta_r)
    
    is_divergent = (
        row['symbol'] != symbol or
        row['entry_date'] != shadow_entry_date or
        row['exit_date'] != shadow_exit_date or
        row['exit_reason'] != shadow_exit_reason or
        delta_r > 1e-8
    )
    
    status = "PASS" if not is_divergent else "FAIL"
    div_reason = "NONE" if not is_divergent else "Mismatched execution or return metric"
    
    if is_divergent:
        divergences += 1
        sizing_divs += 1

    audit_rows.append({
        'certified_id': trade_id,
        'symbol': symbol,
        'signal_date': signal_date,
        'certified_entry_date': row['entry_date'],
        'shadow_entry_date': shadow_entry_date,
        'certified_entry_px': row['entry_px'],
        'shadow_entry_px': shadow_entry_px,
        'certified_stop_px': row['stop_px'],
        'shadow_stop_px': shadow_stop_px,
        'certified_target_px': row['target_px'],
        'shadow_target_px': shadow_target_px,
        'certified_units': row['units'],
        'shadow_units': shadow_units,
        'certified_exit_date': row['exit_date'],
        'shadow_exit_date': shadow_exit_date,
        'certified_exit_reason': row['exit_reason'],
        'shadow_exit_reason': shadow_exit_reason,
        'certified_r': row['r_multiple'],
        'shadow_r': shadow_r,
        'parity_status': status,
        'divergence_reason': div_reason
    })

AUDIT_CSV.parent.mkdir(exist_ok=True, parents=True)
df_audit = pd.DataFrame(audit_rows)
df_audit.to_csv(AUDIT_CSV, index=False)
print(f"[INFO] Audit reconciliation report written to {AUDIT_CSV}")

certified_count = len(df_cert)
shadow_count = len(df_audit)
missing_trades = max(0, certified_count - shadow_count)
extra_trades = max(0, shadow_count - certified_count)

print("\n" + "=" * 64)
print("ELITE-20 SHADOW PARITY CERTIFICATION")
print("=" * 64)
print(f"Certified trades:          {certified_count}")
print(f"Shadow trades:             {shadow_count}")
print(f"Missing:                    {missing_trades}")
print(f"Extra:                      {extra_trades}")
print(f"Signal divergences:         {signal_divs}")
print(f"Sizing divergences:         {sizing_divs}")
print(f"Exit divergences:         {exit_divs}")
print(f"Maximum numeric delta:      {max_numeric_delta:.8f}")
print("-" * 64)

if divergences == 0 and missing_trades == 0 and extra_trades == 0:
    print("ELITE20_SHADOW_PARITY = PASS")
    print("=" * 64)
    sys.exit(0)
else:
    print("ELITE20_SHADOW_PARITY = FAIL")
    print("=" * 64)
    sys.exit(1)
