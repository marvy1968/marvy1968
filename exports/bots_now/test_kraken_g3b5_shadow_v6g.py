#!/usr/bin/env python3
"""
Regression Test Suite for KrakenG3B5ShadowDaemonV6G (Hermetic Sandbox)
Verifies:
1. End-to-end cycle execution with hardened v6G adapter
2. Immediate file initialization on zero-intent days
3. 32-hex cl_ord_id formatting on synthetic entry/exit
4. State persistence in strict fail-closed format
5. Isolated temporary test state sandbox (100% idempotent)
"""

import os
import sys
import json
import tempfile
import pandas as pd
import numpy as np

import kraken_execution_adapter_v6g as adapter
import kraken_g3b5_shadow_v6g as daemon_mod
from kraken_g3b5_shadow_v6g import KrakenG3B5ShadowDaemonV6G
from kraken_execution_adapter_v6g import strict_json_load
import run_stitched_oos_engine as eng

print("=" * 85)
print(" 🛡️ INTEGRATED CANDIDATE DAEMON REGRESSION (v6G + SHADOW)")
print("=" * 85)

real_data_dir = daemon_mod.DATA_DIR

with tempfile.TemporaryDirectory() as tmpdir:
    # Point module-level DATA_DIR to isolated temp directory
    daemon_mod.DATA_DIR = tmpdir
    adapter.DATA_DIR = tmpdir

    manifest_path = os.path.join(real_data_dir, "universe_manifest.csv")
    manifest, dfs = eng.load_data()

    daemon = KrakenG3B5ShadowDaemonV6G(manifest_path=manifest_path)

    # 1. Run a single shadow cycle (flat / zero-intent day test)
    daemon.run_daily_shadow_cycle(dfs=dfs, simulated_nav=100_000.0)

    # 2. Verify journal existence & loading
    assert os.path.exists(daemon.journal_path), f"Journal file missing: {daemon.journal_path}"
    journal_data = strict_json_load(daemon.journal_path)
    print(f"1. JOURNAL INITIALIZATION & INTEGRITY : PASS (Loaded {len(journal_data)} intents)")

    # 3. Synthetic intent injection to test 32-hex formatting & duplicate catch
    cl_id = daemon.client.generate_cl_ord_id("BTC/USD", "2026-08-28", "buy", seq=1)
    entry = daemon.journal_mgr.record_intent(
        cl_ord_id=cl_id,
        symbol="BTC/USD",
        side="buy",
        qty=0.05,
        price=60000.0,
        stop_loss=57000.0,
        dt_str="2026-08-28"
    )

    assert len(entry["cl_ord_id"]) == 32
    assert all(c in "0123456789abcdef" for c in entry["cl_ord_id"])
    print(f"2. 32-HEX cl_ord_id GENERATION        : PASS ({cl_id})")

    # 4. Verify duplicate rejection
    dup_caught = False
    try:
        daemon.journal_mgr.record_intent(cl_id, "BTC/USD", "buy", 0.05, 60000.0, 57000.0, "2026-08-28")
    except ValueError:
        dup_caught = True
    assert dup_caught is True
    print("3. DUPLICATE INTENT GUARD             : PASS (Rejected duplicate cl_ord_id)")

    # 5. Verify positions file integrity
    assert os.path.exists(daemon.positions_path), f"Positions file missing: {daemon.positions_path}"
    pos_data = strict_json_load(daemon.positions_path)
    print(f"4. ACTIVE POSITIONS FILE INTEGRITY    : PASS (Loaded {len(pos_data)} positions)")

    # Restore module paths
    daemon_mod.DATA_DIR = real_data_dir
    adapter.DATA_DIR = real_data_dir

print("=" * 85)
print(" CANDIDATE DAEMON v6G REGRESSION COMPLETE — ALL GATES PASSED")
print("=" * 85)
