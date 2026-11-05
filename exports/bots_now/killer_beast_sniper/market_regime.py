import math
import numpy as np

def calculate_tr_and_dm(candles):
    """Calculates True Range (TR), +DM, and -DM for candle series."""
    tr_list, pos_dm_list, neg_dm_list = [], [], []
    
    for i in range(1, len(candles)):
        h1 = candles[i].get('h', candles[i].get('high', candles[i]['c']))
        l1 = candles[i].get('l', candles[i].get('low', candles[i]['c']))
        c0 = candles[i-1].get('c', candles[i-1].get('close', h1))
        
        h0 = candles[i-1].get('h', candles[i-1].get('high', c0))
        l0 = candles[i-1].get('l', candles[i-1].get('low', c0))
        
        tr = max(h1 - l1, abs(h1 - c0), abs(l1 - c0))
        up_move = h1 - h0
        down_move = l0 - l1
        
        pos_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        neg_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        
        tr_list.append(tr)
        pos_dm_list.append(pos_dm)
        neg_dm_list.append(neg_dm)
        
    return np.array(tr_list), np.array(pos_dm_list), np.array(neg_dm_list)

def calculate_adx(candles, period=14):
    """Calculates ADX (Average Directional Index)."""
    if len(candles) < period * 2:
        return 0.0
        
    tr, pos_dm, neg_dm = calculate_tr_and_dm(candles)
    if len(tr) < period:
        return 0.0

    # Wilder's Smoothing
    smooth_tr = np.sum(tr[:period])
    smooth_pos_dm = np.sum(pos_dm[:period])
    smooth_neg_dm = np.sum(neg_dm[:period])
    
    dx_list = []
    
    for i in range(period, len(tr)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr[i]
        smooth_pos_dm = smooth_pos_dm - (smooth_pos_dm / period) + pos_dm[i]
        smooth_neg_dm = smooth_neg_dm - (smooth_neg_dm / period) + neg_dm[i]
        
        pos_di = (smooth_pos_dm / smooth_tr * 100) if smooth_tr > 0 else 0
        neg_di = (smooth_neg_dm / smooth_tr * 100) if smooth_tr > 0 else 0
        
        di_diff = abs(pos_di - neg_di)
        di_sum = pos_di + neg_di
        
        dx = (di_diff / di_sum * 100) if di_sum > 0 else 0
        dx_list.append(dx)
        
    if not dx_list:
        return 0.0
        
    adx = float(np.mean(dx_list[-period:]))
    return round(adx, 2)

def calculate_choppiness(candles, period=14):
    """Calculates Choppiness Index (CHOP)."""
    if len(candles) < period + 1:
        return 50.0
        
    recent = candles[-(period + 1):]
    tr_sum = 0.0
    max_high = -float('inf')
    min_low = float('inf')
    
    for i in range(1, len(recent)):
        h = recent[i].get('h', recent[i].get('high', recent[i]['c']))
        l = recent[i].get('l', recent[i].get('low', recent[i]['c']))
        c_prev = recent[i-1].get('c', recent[i-1].get('close', h))
        
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_sum += tr
        
        if h > max_high: max_high = h
        if l < min_low: min_low = l
        
    price_range = max_high - min_low
    if price_range <= 0 or tr_sum <= 0:
        return 50.0
        
    chop = 100.0 * (math.log10(tr_sum / price_range) / math.log10(period))
    return round(chop, 2)

def detect_market_regime(candles, period=14):
    """
    Evaluates ADX and Choppiness Index to determine market regime.
    Returns: 'TRENDING', 'RANGING', or 'NEUTRAL'
    """
    if len(candles) < period * 2:
        return "NEUTRAL", 0.0, 50.0
        
    adx = calculate_adx(candles, period=period)
    chop = calculate_choppiness(candles, period=period)
    
    # Classification Logic
    if adx >= 22.0 and chop < 50.0:
        regime = "TRENDING"
    elif adx < 20.0 or chop > 60.0:
        regime = "RANGING"
    else:
        regime = "NEUTRAL"
        
    return regime, adx, chop

if __name__ == "__main__":
    # Quick Test with synthetic candles
    dummy_candles = []
    base_price = 100.0
    for i in range(50):
        base_price += 0.5  # Strong upward trend
        dummy_candles.append({'h': base_price + 0.2, 'l': base_price - 0.2, 'c': base_price})
        
    regime, adx, chop = detect_market_regime(dummy_candles)
    print("=" * 60)
    print("🧪 MARKET REGIME CLASSIFIER TEST")
    print("=" * 60)
    print(f"Resulting Regime : {regime}")
    print(f"ADX Metric       : {adx}")
    print(f"Choppiness Index : {chop}")
    print("=" * 60)
