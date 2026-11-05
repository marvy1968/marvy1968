#!/usr/bin/env python3
"""
End-to-End Integration Suite:
Hardened Adapter v6G + G3-B5 Shadow v3 Engine
"""

import os
import json
import pandas as pd
from datetime import datetime, timezone

from kraken_execution_adapter_v6g import KrakenAPIClient, OrderJournalManager, DATA_DIR, strict_json_load
import run_stitched_oos_engine as eng

TEST_JOURNAL = os.path.join(DATA_DIR, "TEST_shadow_journal.json")
TEST_POSITIONS = os.path.join(DATA_DIR, "TEST_shadow_positions.json")

for p in [TEST_JOURNAL, TEST_POSITIONS]:
    if os.path.exists(p):
        os.remove(p)

print("=" * 85)
print(" 🔄 END-TO-END SHADOW DAEMON + HARDENED ADAPTER v6G INTEGRATION SUITE")
print("=" * 85)

# 1. Initialize API Client
client = KrakenAPIClient()
manifest, dfs = eng.load_data()

# 2. Instantiate Journal Manager & Test 32-Hex Intent Logging
jm = OrderJournalManager(journal_path=TEST_JOURNAL, positions_path=TEST_POSITIONS)
cl_id = client.generate_cl_ord_id("SOL/USD", "2026-08-28", "buy", seq=1)

entry = jm.record_intent(
    cl_ord_id=cl_id,
    symbol="SOL/USD",
    side="buy",
    qty=2.5,
    price=145.20,
    stop_loss=138.00,
    dt_str="2026-08-28"
)

assert os.path.exists(TEST_JOURNAL)
assert entry["status"] == "INTENT_RECORDED"
assert entry["cl_ord_id"] == cl_id
assert len(cl_id) == 32
print(f"1. SHADOW INTENT LOGGING              : PASS (Logged 32-hex ID: {cl_id})")

# 3. Simulate Restart Reconciliation
mock_fill = {
    cl_id: {"txid": "TX_SHADOW_001", "price": 145.25, "vol": 2.5}
}
rec_res = jm.reconcile_on_restart(mock_broker_open_orders={}, mock_broker_trades=mock_fill)

assert rec_res["reconciled_count"] == 1
assert "SOL/USD" in jm.positions
assert jm.positions["SOL/USD"]["qty"] == 2.5
assert jm.positions["SOL/USD"]["entry_px"] == 145.25
print("2. RESTART RECONCILIATION             : PASS (Reconstructed SOL/USD position)")

# 4. Strict State Verification
loaded_positions = strict_json_load(TEST_POSITIONS)
assert "SOL/USD" in loaded_positions
print("3. STRICT STATE VALIDATION            : PASS (Verified on-disk consistency)")

# Cleanup
for p in [TEST_JOURNAL, TEST_POSITIONS]:
    if os.path.exists(p):
        os.remove(p)

print("=" * 85)
print(" INTEGRATION PREFLIGHT COMPLETE — ZERO ERRORS")
print("=" * 85)
