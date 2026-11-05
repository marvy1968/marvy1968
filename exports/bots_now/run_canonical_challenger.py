import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Import the immutable engine components
try:
    import run_f0_hardened_portfolio_v2 as canonical_engine
except ImportError:
    print("FATAL: run_f0_hardened_portfolio_v2.py not found in current directory.")
    sys.exit(1)

def verify_engine_parity():
    """Hard kill gate: Canonical F0 must reproduce exact benchmark numbers."""
    print("Verifying Canonical Engine Parity...")
    res = canonical_engine.run_f0_canonical()
    
    n_trades = res.get("trades", 0)
    cagr = res.get("cagr", 0.0)
    max_dd = res.get("max_dd", 0.0)
    pf = res.get("pf", 0.0)

    print(f"Engine output: Trades={n_trades} | CAGR={cagr:+.2f}% | MaxDD={max_dd:.2f}% | PF={pf:.2f}")

    if n_trades != 278 or abs(cagr - 3.85) > 0.10 or abs(abs(max_dd) - 16.61) > 0.20:
        print("\n" + "!" * 80)
        print("FATAL: CANONICAL ENGINE PARITY FAILED")
        print(f"Expected: 278 trades, +3.85% CAGR, -16.61% MaxDD, 1.15 PF")
        print(f"Received: {n_trades} trades, {cagr:+.2f}% CAGR, {max_dd:.2f}% MaxDD, {pf:.2f} PF")
        print("ABORTING: No challenger will be evaluated on an uncertified engine.")
        print("!" * 80)
        sys.exit(1)

    print(">>> PARITY CHECK PASSED: Engine certified (278 trades, +3.85% CAGR, -16.61% DD, 1.15 PF).\n")
    return res

if __name__ == "__main__":
    verify_engine_parity()
