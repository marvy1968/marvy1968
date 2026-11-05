import re

SHADOW_PATH = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"

with open(SHADOW_PATH, "r") as f:
    code = f.read()

# Define the 50-Candle Auto-Tuner Module
AUTO_TUNER_MODULE = '''
# ==============================================================================
# 🧠 50-CANDLE AUTO-PARAMETER TUNER MODULE
# ==============================================================================
from collections import defaultdict
import numpy as np

CANDLE_BUFFERS = defaultdict(list)
CANDLE_COUNTERS = defaultdict(int)

TUNER_BOUNDS = {
    "STOCKS": {"min_sl": 0.0002, "max_sl": 0.0020, "min_tp": 0.0003, "max_tp": 0.0040},
    "CRYPTO": {"min_sl": 0.0100, "max_sl": 0.0350, "min_tp": 0.0200, "max_tp": 0.0800},
    "FOREX":  {"min_sl": 0.0001, "max_sl": 0.0010, "min_tp": 0.0002, "max_tp": 0.0020}
}

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(candles)):
        high = candles[i].get('h', candles[i].get('high', candles[i]['c']))
        low = candles[i].get('l', candles[i].get('low', candles[i]['c']))
        prev_close = candles[i-1].get('c', candles[i-1].get('close', high))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    return float(np.mean(tr_list[-period:]))

def update_50_candle_tuner(symbol, current_price, candle_data, asset_class="STOCKS"):
    """
    Tracks candles per asset and recalculates SL/TP every 50 completed bars.
    """
    CANDLE_BUFFERS[symbol].append(candle_data)
    if len(CANDLE_BUFFERS[symbol]) > 100:
        CANDLE_BUFFERS[symbol].pop(0)

    CANDLE_COUNTERS[symbol] += 1

    # Execute recalculation every 50 candles
    if CANDLE_COUNTERS[symbol] % 50 == 0:
        atr = calculate_atr(CANDLE_BUFFERS[symbol], period=14)
        if atr <= 0 or current_price <= 0:
            return None, None

        atr_pct = atr / current_price
        raw_sl = round(atr_pct * 1.2, 5)
        raw_tp = round(atr_pct * 2.0, 5)

        b = TUNER_BOUNDS.get(asset_class, TUNER_BOUNDS["STOCKS"])
        new_sl = max(min(raw_sl, b["max_sl"]), b["min_sl"])
        new_tp = max(min(raw_tp, b["max_tp"]), b["min_tp"])

        print(f"🧠 [AUTO-TUNE 50C] {symbol:<8} ({asset_class}) | Price: ${current_price:,.2f} | ATR: {atr:.4f} => New SL: {new_sl*100:.3f}% | New TP: {new_tp*100:.3f}%")
        return new_sl, new_tp

    return None, None
# ==============================================================================
'''

# Check if tuner is already injected
if "update_50_candle_tuner" not in code:
    code = AUTO_TUNER_MODULE + "\n" + code
    with open(SHADOW_PATH, "w") as f:
        f.write(code)
    print("✅ Successfully injected 50-Candle AutoTuner into multi_scalper_shadow.py!")
else:
    print("ℹ️ AutoTuner module already injected into multi_scalper_shadow.py.")
