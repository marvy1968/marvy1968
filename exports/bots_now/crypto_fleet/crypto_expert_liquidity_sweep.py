#!/usr/bin/env python3
"""
INSTITUTIONAL EXPERT MODULE #2: LIQUIDITY SWEEP REJECTION
---------------------------------------------------------
Identifies micro liquidity sweeps above 15-period swing highs or below
swing lows that immediately reject back into the range.
"""

import ccxt
import pandas as pd
import numpy as np

def detect_liquidity_sweep(exchange, symbol="BTC/USD"):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=30)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calculate recent 15-period swing high and low (excluding current bar)
        swing_high = df['high'].iloc[-16:-1].max()
        swing_low = df['low'].iloc[-16:-1].min()
        
        latest = df.iloc[-1]
        
        # Bullish Sweep (Bear Trap): Swept swing low but closed above it
        swept_low_rejection = (latest['low'] < swing_low) and (latest['close'] > swing_low)
        
        # Bearish Sweep (Bull Trap): Swept swing high but closed below it
        swept_high_rejection = (latest['high'] > swing_high) and (latest['close'] < swing_high)
        
        return {
            'symbol': symbol,
            'swing_high': round(swing_high, 2),
            'swing_low': round(swing_low, 2),
            'latest_close': round(latest['close'], 2),
            'bear_trap_sweep': swept_low_rejection,
            'bull_trap_sweep': swept_high_rejection
        }
    except Exception as e:
        print(f"Liquidity Sweep Error: {e}")
        return None

if __name__ == "__main__":
    exchange = ccxt.coinbase({'enableRateLimit': True})
    res = detect_liquidity_sweep(exchange, "BTC/USD")
    print(f"🎯 Liquidity Sweep Analysis:\n{res}")
