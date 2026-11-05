
# ==============================================================================
# 🔓 24-HOUR TEMPORARY UNBRACKETED TELEMETRY MODE
# ==============================================================================
from datetime import datetime

EXPIRATION_TIMESTAMP = "2026-08-12 06:19:26"

def is_unbracketed_active():
    """Checks if the 24-hour unbracketed window is currently active."""
    expiry = datetime.strptime(EXPIRATION_TIMESTAMP, "%Y-%m-%d %H:%M:%S")
    return datetime.now() < expiry
# ==============================================================================

import os
import requests
import json
import oandapyV20
import oandapyV20.endpoints.orders as orders
from oandapyV20.contrib.requests import MarketOrderRequest, TakeProfitDetails, StopLossDetails

# --- ENVIRONMENT CONFIGURATION ---
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_TOKEN  = os.getenv("OANDA_API_TOKEN", "1b699d66d2894fb3e0b8f367c")
OANDA_ENV        = os.getenv("OANDA_ENV", "practice")
DRY_RUN          = os.getenv("DRY_RUN", "true").lower() == "true"

# Default Forex Pip Distances for Bracket Orders
SL_TP_PIP_MATRIX = {
    "EUR_USD": {"sl_pips": 12, "tp_pips": 24},
    "GBP_USD": {"sl_pips": 15, "tp_pips": 30},
    "USD_JPY": {"sl_pips": 15, "tp_pips": 30},
    "AUD_USD": {"sl_pips": 10, "tp_pips": 20},
    "USD_CAD": {"sl_pips": 12, "tp_pips": 24},
}

def calculate_bracket_prices(instrument, side, current_price, sl_pips, tp_pips):
    """Calculates absolute SL and TP prices based on pip distances."""
    pip_factor = 0.01 if "JPY" in instrument else 0.0001
    
    if side.upper() == "BUY":
        sl_price = round(current_price - (sl_pips * pip_factor), 5)
        tp_price = round(current_price + (tp_pips * pip_factor), 5)
    else:
        sl_price = round(current_price + (sl_pips * pip_factor), 5)
        tp_price = round(current_price - (tp_pips * pip_factor), 5)
        
    return sl_price, tp_price

def execute_oanda_forex_order(instrument, side, price=None, units=1000):
    """
    Submits a market order with dynamic SL/TP brackets to OANDA v20 Practice API.
    """
    # Normalize instrument naming (e.g. 'EUR/USD' -> 'EUR_USD')
    symbol = instrument.replace("/", "_")
    
    # Retrieve pip targets or fallback to defaults
    pip_cfg = SL_TP_PIP_MATRIX.get(symbol, {"sl_pips": 15, "tp_pips": 30})
    
    # If live price is provided, construct bracket orders
    sl_price, tp_price = None, None
    if price and price > 0:
        sl_price, tp_price = calculate_bracket_prices(symbol, side, price, pip_cfg["sl_pips"], pip_cfg["tp_pips"])

    # Signed unit count: positive for BUY, negative for SELL
    units_signed = units if side.upper() == "BUY" else -units

    if DRY_RUN:
        print(f"🧪 [OANDA DRY-RUN] {symbol:<7} | Side: {side:<4} | Units: {units_signed:>6} | Entry: {price} | SL: {sl_price} | TP: {tp_price}")
        return {"status": "DRY_RUN"}

    try:
        client = oandapyV20.API(access_token=OANDA_API_TOKEN, environment=OANDA_ENV)
        
        # Build Order Specification
        order_spec = {
            "order": {
                "units": str(units_signed),
                "instrument": symbol,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }

        # Attach SL/TP Brackets if available
        if sl_price and tp_price:
            order_spec["order"]["stopLossOnFill"] = {"timeInForce": "GTC", "price": str(sl_price)}
            order_spec["order"]["takeProfitOnFill"] = {"timeInForce": "GTC", "price": str(tp_price)}

        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=order_spec)
        response = client.request(r)
        
        fill_data = response.get("orderFillTransaction", {})
        fill_price = fill_data.get("price", price)
        print(f"✅ [OANDA FOREX FILLED] {symbol:<7} | Side: {side:<4} | Units: {units_signed:>6} | Fill Price: {fill_price}")
        return response

    except oandapyV20.exceptions.V20Error as err:
        print(f"❌ [OANDA EXECUTION ERROR] {symbol}: {err}")
        return None
    except Exception as e:
        print(f"❌ [OANDA UNEXPECTED ERROR] {symbol}: {e}")
        return None

if __name__ == "__main__":
    print("=" * 75)
    print("🌐 OANDA FOREX EXECUTOR INITIALIZED")
    print(f"  • Account ID  : {OANDA_ACCOUNT_ID}")
    print(f"  • Environment : {OANDA_ENV}")
    print(f"  • DRY_RUN     : {DRY_RUN}")
    print("=" * 75)
    
    # Test Dry-Run Execution
    execute_oanda_forex_order("EUR_USD", "BUY", price=1.0850, units=1000)
