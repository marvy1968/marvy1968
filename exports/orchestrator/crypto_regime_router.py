import numpy as np

def calculate_efficiency_ratio(prices, period=14):
    if len(prices) < period + 1:
        return 0.0
    net_change = abs(prices[-1] - prices[-period-1])
    sum_changes = sum(abs(prices[i] - prices[i-1]) for i in range(-period, 0))
    return net_change / sum_changes if sum_changes > 0 else 0.0

def calculate_hurst_exponent(prices):
    if len(prices) < 30:
        return 0.5
    lags = range(2, 20)
    tau = [np.sqrt(np.std(np.subtract(prices[lag:], prices[:-lag]))) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return poly[0] * 2.0

def classify_regime(prices):
    er = calculate_efficiency_ratio(prices)
    h = calculate_hurst_exponent(prices)
    
    if er >= 0.55 and h > 0.52:
        return "TREND", er, h
    elif er <= 0.30 or h < 0.48:
        return "CHOP", er, h
    else:
        return "SQUEEZE", er, h
