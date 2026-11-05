import re

SHADOW_PATH = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"

with open(SHADOW_PATH, "r") as f:
    code = f.read()

# 1. Inject import for oanda_forex_executor
IMPORT_STMT = "from oanda_forex_executor import execute_oanda_forex_order\n"

if "execute_oanda_forex_order" not in code:
    code = IMPORT_STMT + code

# 2. Add Forex Routing Handler
FOREX_ROUTER_MODULE = '''
# ==============================================================================
# 🌐 FOREX OANDA ROUTING BRIDGE
# ==============================================================================
FOREX_SYMBOLS = {"EUR_USD", "USD_JPY", "AUD_USD", "USD_CAD", "GBP_USD", "EUR/USD", "USD/JPY", "AUD/USD", "USD/CAD", "GBP/USD"}

def route_forex_signal_to_oanda(symbol, side, price, strategy_name):
    """
    Checks if symbol is a Forex pair and routes order to OANDA Executor.
    """
    if symbol in FOREX_SYMBOLS:
        print(f"⚡ [ROUTING TO OANDA] Symbol: {symbol} | Side: {side} | Strat: {strategy_name} | Price: {price}")
        execute_oanda_forex_order(instrument=symbol, side=side, price=price, units=1000)
        return True
    return False
# ==============================================================================
'''

if "route_forex_signal_to_oanda" not in code:
    code = FOREX_ROUTER_MODULE + "\n" + code
    with open(SHADOW_PATH, "w") as f:
        f.write(code)
    print("✅ Injected route_forex_signal_to_oanda router into multi_scalper_shadow.py!")
else:
    print("ℹ️ Forex router module already present.")

