import numpy as np

# 1. ALPHA LAYER: Statistical Deviation from Mean (Z-Score)
def get_institutional_signal(df):
    volatility = df['price'].rolling(20).std()
    deviation = (df['price'] - df['vwap']) / volatility
    
    # Capture mean-reversion at 1.5 Standard Deviations
    if deviation.iloc[-1] > 1.5: return "SELL_LIMIT"
    if deviation.iloc[-1] < -1.5: return "BUY_LIMIT"
    return "WAIT"

# 2. RISK GOVERNOR: Dynamic Beta Adjustment
def get_position_size(balance, daily_dd):
    # If Drawdown > 1.5%, scale down risk exposure by 50%
    if daily_dd > 0.015:
        return 0.5 
    return 1.0

# 3. EXECUTION: Limit-Order Logic
def execute_institutional_order(side, symbol, size):
    # Logic to route to OANDA via LIMIT_ORDER to capture spread
    print(f"✅ [INSTITUTIONAL] Routing {side} LIMIT_ORDER for {symbol} at Size {size}")
