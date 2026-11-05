#!/usr/bin/env python3
"""
MASTER CRYPTO FLEET DAILY SCORECARD
"""

import os
import json
import datetime
import pandas as pd

STATE_FILE = "/home/moonrider1968/bots/crypto_fleet/crypto_regime_state.json"

def print_scorecard():
    print("==================================================")
    print("            LIVE CRYPTO FLEET SCORECARD           ")
    print("==================================================")
    
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        print(f" Macro BTC Regime  : {state.get('btc_regime', 'UNKNOWN')}")
        print(f" Account NAV (USDT): ${state.get('account_nav_usdt', 0.0):,.2f}")
        print(f" Session ROI       : {state.get('session_roi_pct', 0.0):+.2f}%")
        print(f" Peak Session ROI  : +{state.get('peak_roi_pct', 0.0):.2f}%")
        print(f" Brain Multiplier  : {state.get('sizing_multiplier', 1.0)}x")
        print(f" Trailing Floor    : {state.get('stop_floor_pct', -2.5):.2f}%")
        print(f" Last Brain Sync   : {state.get('updated_at', 'N/A')}")
    else:
        print("⚠️ Waiting for crypto_master_brain.py to initialize state...")
        
    print("==================================================")

if __name__ == "__main__":
    print_scorecard()
