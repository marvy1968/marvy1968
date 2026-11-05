#!/usr/bin/env python3
"""
INSTITUTIONAL GATEKEEPER
------------------------
Imports dark_pool_tracker and provides a safety check function for stock bot execution.
"""

import sys
import os

# Ensure bot folder is in python path
sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")

from dark_pool_tracker import scan_dark_pool_blocks

def evaluate_institutional_gate(symbol, min_notional=500000):
    """
    Returns True if safe to execute trade, False if institutional block volume is detected.
    """
    dp_data = scan_dark_pool_blocks(symbol, min_notional=min_notional)
    
    if dp_data and dp_data.get("institutional_block"):
        notional = dp_data.get("notional", 0)
        print(f"🚨 INSTITUTIONAL GATE TRIGGERED: High volume detected on {symbol} (${notional:,.2f}). Holding order.")
        return False  # Block trade entry
        
    print(f"✅ INSTITUTIONAL GATE PASSED: {symbol} is safe to trade.")
    return True  # Safe to trade

if __name__ == "__main__":
    # Diagnostic test on SPY
    test_symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    is_safe = evaluate_institutional_gate(test_symbol)
    print(f"Gate Status for {test_symbol}: Safe={is_safe}")
