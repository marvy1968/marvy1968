
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import glob
import re
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

print("🔍 SEARCHING SERVER FOR SAVED ALPACA KEYS...")
found_keys = set()

# Search across all bot subdirectories for potential API Key patterns
search_paths = [
    "/home/moonrider1968/bots/**/*.py",
    "/home/moonrider1968/bots/**/*.env*",
    "/home/moonrider1968/bots/**/*.json",
    "/home/moonrider1968/bots/**/*.sh"
]

for pattern in search_paths:
    for filepath in glob.glob(pattern, recursive=True):
        if "venv" in filepath: continue
        try:
            with open(filepath, "r", errors="ignore") as f:
                content = f.read()
                # Find Alpaca key formats (starts with PK or AK followed by alphanumeric chars)
                matches = re.findall(r'["\'](PK[A-Z0-0]{15,35}|AK[A-Z0-9]{15,35})["\']', content)
                for key in matches:
                    # Find potential secrets near the key
                    found_keys.add(key)
        except: pass

print(f"📌 Found {len(found_keys)} potential API Key identifiers in server files.")

# Test list of known candidate keys
candidate_pairs = [
    ("REDACTED_BROKER_KEY", "HnPTMXNVms87C7QgZ9RrvnjmxPvhaq8tM2LxgndztXnx"),
    ("REDACTED_BROKER_KEY", "25RzsZWyCvsygn94dfYAVDRwb8c6YZviPm8TXRXp7Lhq")
]

print("\n--- TESTING KEY AUTHENTICATION MATRIX ---")
for key, secret in candidate_pairs:
    print(f"\nTesting Key: {key[:8]}...")
    
    # Test Paper Mode
    try:
        tc_paper = TradingClient(key, secret, paper=True)
        acct = tc_paper.get_account()
        print(f"  ✅ PAPER TRADING SUCCESS! Equity: ${float(acct.equity):,.2f}")
    except Exception as e:
        print(f"  ❌ Paper Trading: {e}")
        
    # Test Live Mode
    try:
        tc_live = TradingClient(key, secret, paper=False)
        acct = tc_live.get_account()
        print(f"  ✅ LIVE TRADING SUCCESS! Equity: ${float(acct.equity):,.2f}")
    except Exception as e:
        print(f"  ❌ Live Trading: {e}")

