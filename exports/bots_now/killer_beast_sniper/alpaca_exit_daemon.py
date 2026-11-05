
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
import time
import requests

APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

PROFIT_TARGET_PCT = 0.015   # Lock in profits at +1.5%
STOP_LOSS_PCT = -0.012     # Cut losses at -1.2%

def close_position(symbol):
    url = f"{BASE_URL}/positions/{symbol}"
    try:
        r = requests.delete(url, headers=HEADERS, timeout=5)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"Error closing {symbol}: {e}")
        return False

def manage_positions():
    try:
        r = requests.get(f"{BASE_URL}/positions", headers=HEADERS, timeout=5)
        if r.status_code != 200:
            return
        
        positions = r.json()
        for p in positions:
            symbol = p.get("symbol")
            unrealized_plpc = float(p.get("unrealized_plpc", 0))
            
            # 1. Take Profit Check
            if unrealized_plpc >= PROFIT_TARGET_PCT:
                print(f"🎯 [TAKE PROFIT TRIGGERED] {symbol} | Gain: +{unrealized_plpc*100:.2f}%")
                close_position(symbol)
                
            # 2. Stop Loss Check
            elif unrealized_plpc <= STOP_LOSS_PCT:
                print(f"🛡️ [STOP LOSS TRIGGERED] {symbol} | Drawdown: {unrealized_plpc*100:.2f}%")
                close_position(symbol)
    except Exception as e:
        print(f"Position manager check error: {e}")

def main():
    print("🚀 Active Alpaca Position Exit Daemon Online (1.5% TP / -1.2% SL)...")
    while True:
        manage_positions()
        time.sleep(10)

if __name__ == "__main__":
    main()
