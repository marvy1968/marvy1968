#!/usr/bin/env python3
"""
Test Suite for Adaptive Closed-Loop Engine v2:
Validates deduplication, shadow promotion gate, and penalty box cooldowns.
"""

import os
import json
from adaptive_learning_engine_v2 import AdaptiveClosedLoopManager, DATA_DIR

TEST_LEDGER = os.path.join(DATA_DIR, "TEST_v2_ledger.json")
TEST_POLICY = os.path.join(DATA_DIR, "TEST_v2_policy.json")

for p in [TEST_LEDGER, TEST_POLICY]:
    if os.path.exists(p):
        os.remove(p)

manager = AdaptiveClosedLoopManager(ledger_path=TEST_LEDGER, policy_path=TEST_POLICY)

print("=" * 90)
print(" 🔬 TESTING ENGINE v2: DEDUPLICATION, PENALTY BOX & SHADOW PROMOTION")
print("=" * 90)

# 1. Test Ingestion of 20 WEAK_BULL Losses
for i in range(20):
    trade = {
        "symbol": "SUI/USD",
        "timestamp": f"2026-03-{i+1:02d}",
        "entry_px": 2.50,
        "exit_dt": f"2026-03-{i+2:02d}",
        "btc_momentum_bucket": "WEAK_BULL",
        "volatility_bucket": "HIGH_VOL",
        "realized_pnl": -40.0,
        "r_multiple": -1.0,
        "win": False
    }
    success, msg = manager.record_completed_trade(trade)

print(f"Status after 20 trades: {msg}")

# 2. Verify Shadow Policy State
print("\n--- ACTIVE POLICY AFTER PROMOTION GUARD ---")
print(json.dumps(manager.policy, indent=2))

# 3. Verify Penalty Box
is_sui_active = manager.is_symbol_eligible("SUI/USD", "2026-03-23")
print(f"\nIs SUI/USD eligible on 2026-03-23 (during cooldown)? : {is_sui_active} (Expected: False)")

# 4. Verify Deduplication
dup_trade = {
    "symbol": "SUI/USD",
    "timestamp": "2026-03-01",
    "entry_px": 2.50,
    "exit_dt": "2026-03-02",
    "btc_momentum_bucket": "WEAK_BULL",
    "volatility_bucket": "HIGH_VOL",
    "realized_pnl": -40.0,
    "r_multiple": -1.0,
    "win": False
}
success, dup_msg = manager.record_completed_trade(dup_trade)
print(f"Deduplication check: {dup_msg}")

print("\n--- INVARIANTS CHECK ---")
assert manager.policy["btc_risk_multipliers"]["WEAK_BULL"] == 0.625
print("SHADOW PROMOTION GATE : PASS (Throttled WEAK_BULL to 0.25% floor)")
assert is_sui_active is False
print("PENALTY BOX COOLDOWN  : PASS")
assert success is False
print("DEDUPLICATION ENGINE  : PASS")
print("=" * 90)
