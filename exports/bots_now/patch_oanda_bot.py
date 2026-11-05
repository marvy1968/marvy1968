import os

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

# New logic to inject
patch_logic = """
# --- OPTIMIZED REGIME-AWARE ENGINE ---
import numpy as np

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    return ranges.max(axis=1).rolling(period).mean()

def get_optimized_signal(df):
    # Filter 1: No-Trade Zone (ADX < 20 means range-bound noise)
    if df['adx'].iloc[-1] < 20: return "WAIT"
    
    # Filter 2: Momentum & Mean Reversion
    if df['price'].iloc[-1] < df['vwap'].iloc[-1] and df['rsi'].iloc[-1] < 35:
        return "BUY"
    if df['price'].iloc[-1] > df['vwap'].iloc[-1] and df['rsi'].iloc[-1] > 65:
        return "SELL"
    return "WAIT"

def calculate_size(balance, atr):
    # Risk 1% of balance per trade
    risk_amount = balance * 0.01
    return int(risk_amount / (atr * 100))
# -------------------------------------
"""

with open(bot_path, "r") as f:
    content = f.read()

# Inject the logic
if "# --- OPTIMIZED REGIME-AWARE ENGINE ---" not in content:
    with open(bot_path, "w") as f:
        f.write(patch_logic + content)
    print(f"✅ OANDA Beast Optimized.")
