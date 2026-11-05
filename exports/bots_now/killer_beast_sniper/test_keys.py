
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
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

# 1. Check if .env exists and print keys if present
if os.path.exists('.env'):
    print("📄 Found .env file! Reading contents...")
    with open('.env') as f:
        print(f.read())
else:
    print("ℹ️ No .env file in current directory.")

KEY = "REDACTED_BROKER_KEY"
SECRET = "REDACTED"

print("\n--- Testing Trading Account Client ---")
try:
    tc = TradingClient(KEY, SECRET, paper=True)
    acct = tc.get_account()
    print(f"✅ TRADING SUCCESS: Balance = ${float(acct.equity):,.2f}")
except Exception as e:
    print(f"❌ TRADING ERROR: {e}")

print("\n--- Testing Stock Data Client ---")
try:
    hc = StockHistoricalDataClient(KEY, SECRET)
    req = StockBarsRequest(symbol_or_symbols=["AAPL"], timeframe=TimeFrame(1, TimeFrameUnit.Day), limit=5, feed=DataFeed.IEX)
    res = hc.get_stock_bars(req)
    print(f"✅ DATA SUCCESS: Fetched {len(res.df)} bars for AAPL")
except Exception as e:
    print(f"❌ DATA ERROR: {e}")
