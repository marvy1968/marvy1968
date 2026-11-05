#!/usr/bin/env python3
"""
SAFE validation harness for AdaptiveClosedLoopManager.
Uses isolated TEST ledger/policy files.
Does NOT touch the production adaptive-learning state.
"""

import os
import json
import adaptive_learning_engine as ale

TEST_LEDGER = os.path.join(ale.DATA_DIR, "TEST_trade_telemetry_ledger.json")
TEST_POLICY = os.path.join(ale.DATA_DIR, "TEST_active_adaptive_policy.json")

# Redirect module globals BEFORE manager construction
ale.TELEMETRY_LOG_PATH = TEST_LEDGER
ale.POLICY_STATE_PATH = TEST_POLICY

# Start clean
for path in [TEST_LEDGER, TEST_POLICY]:
    if os.path.exists(path):
        os.remove(path)

manager = ale.AdaptiveClosedLoopManager()

print("=" * 90)
print(" CLOSED-LOOP LEARNER — SAFE ISOLATED VALIDATION")
print("=" * 90)

baseline = manager.get_effective_risk_pct({
    "btc_momentum_bucket": "WEAK_BULL",
    "volatility_bucket": "HIGH_VOL",
})
print(f"Baseline WEAK_BULL/HIGH_VOL risk: {baseline * 100:.3f}%")

print("\nInjecting 20 synthetic WEAK_BULL / HIGH_VOL losses...")
for i in range(20):
    day = i + 1
    exit_day = i + 2
    sim_trade = {
        "symbol": "SUI/USD",
        "timestamp": f"2026-03-{day:02d}",
        "nav_at_entry": 100000.0,
        "btc_momentum_bucket": "WEAK_BULL",
        "volatility_bucket": "HIGH_VOL",
        "liquidity_tier": "TIER_2_ALT",
        "donch_breakout_strength": 0.015,
        "atr_pct": 0.09,
        "med_vol_30d": 800000.0,
        "entry_px": 2.50,
        "stop_px": 2.30,
        "initial_risk_prs": 0.20,
        "qty": 200.0,
        "exit_dt": f"2026-03-{exit_day:02d}",
        "exit_px": 2.28,
        "exit_reason": "atr_stop",
        "realized_pnl": -44.0,
        "r_multiple": -1.05,
        "win": False,
        "mae_pct": 0.088,
        "mfe_pct": 0.012,
        "duration_bars": 1,
    }
    manager.record_completed_trade(sim_trade)

print("\n--- TEST POLICY STATE ---")
print(json.dumps(manager.policy, indent=2))

effective = manager.get_effective_risk_pct({
    "btc_momentum_bucket": "WEAK_BULL",
    "volatility_bucket": "HIGH_VOL",
})

print(f"\nResulting WEAK_BULL/HIGH_VOL risk: {effective * 100:.3f}%")

print("\n--- INVARIANTS ---")
assert ale.MIN_RISK_PER_TRADE <= effective <= ale.MAX_RISK_PER_TRADE
print("RISK HARD CLAMP       : PASS")
assert len(manager.ledger) == 20
print("LEDGER COUNT          : PASS")
assert effective == ale.MIN_RISK_PER_TRADE
print("NEGATIVE BUCKET CUT   : PASS")
assert TEST_LEDGER != os.path.join(ale.DATA_DIR, "trade_telemetry_ledger.json")
assert TEST_POLICY != os.path.join(ale.DATA_DIR, "active_adaptive_policy.json")
print("TEST/LIVE ISOLATION   : PASS")

print("\n" + "=" * 90)
print("SAFE UNIT TEST COMPLETE")
print("=" * 90)
