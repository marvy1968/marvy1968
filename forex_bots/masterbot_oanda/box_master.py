import numpy as np

def establish_range(df, lookback=20):
    """Establishes the High/Low boundaries of the 'Box'"""
    df['range_high'] = df['high'].rolling(window=lookback).max()
    df['range_low'] = df['low'].rolling(window=lookback).min()
    df['midline'] = (df['range_high'] + df['range_low']) / 2
    return df.iloc[-1]

def get_box_signal(current_price, range_data, position_side=None):
    """
    Logic: 
    1. If price hits High -> SHORT (Target Midline)
    2. If price hits Low  -> LONG  (Target Midline)
    3. If price BREAKS High + 0.1% -> STOP & FLIP LONG (Trend)
    4. If price BREAKS Low - 0.1%  -> STOP & FLIP SHORT (Trend)
    """
    high = range_data['range_high']
    low = range_data['range_low']
    mid = range_data['midline']
    
    # 1. BREAKOUT FLIP (The 'Stop and Reverse')
    if current_price > (high * 1.001):
        return "BREAKOUT_LONG", "Stage 2 Trend Flip"
    if current_price < (low * 0.999):
        return "BREAKOUT_SHORT", "Stage 4 Trend Flip"

    # 2. RANGE PING-PONG (The Mean Reversion)
    if position_side is None:
        if current_price >= high:
            return "RANGE_SHORT", "Shorting Range High"
        if current_price <= low:
            return "RANGE_LONG", "Buying Range Low"
    
    # 3. EXIT AT MIDLINE
    if (position_side == "LONG" and current_price >= mid) or \
       (position_side == "SHORT" and current_price <= mid):
        return "EXIT_MIDLINE", "Target Hit"

    return "HOLD", "Inside Box"
