import pandas as pd
import numpy as np

def assess_regime(df):
    # Expert Logic: Bollinger Band Width for Range vs Trend
    bb_width = (df['upper'] - df['lower']) / df['sma']
    is_ranging = bb_width < bb_width.rolling(20).mean()
    return "RANGE" if is_ranging.iloc[-1] else "TREND"

def decide_action(regime, rsi, macd, vwap, price):
    # Expert Combo Logic
    if regime == "RANGE":
        if rsi < 30 and price < vwap: return "BUY"
        if rsi > 70 and price > vwap: return "SELL"
    return "WAIT"
