
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

# --- LINKED TO UNIFIED BRAIN ---
import sys
sys.path.append('/home/moonrider1968/bots/Unified_Brain')
from brain import assess_regime, decide_action
# -------------------------------
import time
import datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
PAPER = True

api = TradingClient(API_KEY, SECRET_KEY, paper=PAPER)

def get_stock_state():
    try:
        acc = api.get_account()
        all_pos = api.get_all_positions()
        stock_pos = {p.symbol: p for p in all_pos if p.asset_class != AssetClass.CRYPTO and "/" not in p.symbol and "USD" not in p.symbol}
        
        return {
            "liquid_cash": float(acc.cash),
            "positions": stock_pos
        }
    except Exception as e:
        print(f"❌ Stock Ingestion Failure: {e}"); return None

def phase_4_execute_orders(state, watchlist):
    positions = state["positions"]
    cash_pool = state["liquid_cash"]

    # LOUD TELEMETRY: Always report the exact metrics driving the decisions
    print(f"🔍 [Telemetry] Live Cash Pool: ${cash_pool:,.2f} | Open Stocks: {len(positions)}/{MAX_POSITIONS}")

    if len(positions) >= MAX_POSITIONS:
        print("ℹ️ [Telemetry] Stock slots are completely full. Skipping entry scans.")
        return

    if cash_pool <= 100:
        print("⚠️ [Telemetry] Cash pool is below the $100 baseline. Entry system locked to prevent overdrafts.")
        return

    for sym in watchlist:
        if sym not in positions:
            try:
                target_notional = min(50.00, cash_pool - 20)
                if target_notional < 1.00:
                    print(f"⚠️ [Telemetry] Calculated notional value (${target_notional:.2f}) for {sym} is too low. Skipping.")
                    continue
                    
                print(f"📡 [Telemetry] Attempting Cash-Notional Route for {sym} | Size: ${target_notional:.2f}")
                api.submit_order(MarketOrderRequest(
                    symbol=sym,
                    notional=target_notional,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY
                ))
                print(f"🔥 Micro-Fractional Stock Filled: {sym} | Value ${target_notional:.2f}")
                break
            except Exception as e: 
                print(f"❌ Entry Fail {sym}: {e}")

def main():
    print(f"🚀 Stock Sandbox v2.0 (Verbose Telemetry) Active @ {datetime.datetime.now()}")
    while True:
        try:
            state = get_stock_state()
            if state:
                phase_4_execute_orders(state, WATCHLIST)
            time.sleep(15)
        except Exception as e: print(f"🚨 Loop Error: {e}"); time.sleep(10)

if __name__ == "__main__":
    main()

api = TradingClient('REDACTED_BROKER_KEY', '2u2XDxxT8us84n1aCJi7toVKoJUfu7Gi2Vpij2bgHvfJ', paper=True)
