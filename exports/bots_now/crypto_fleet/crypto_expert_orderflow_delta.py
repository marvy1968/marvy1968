#!/usr/bin/env python3
"""
INSTITUTIONAL EXPERT MODULE: ORDER FLOW DELTA & VOLUME IMBALANCE
-----------------------------------------------------------------
Evaluates taker buy/sell volume delta and orderbook bid/ask imbalance.
Uses Coinbase Advanced Trade public endpoints (No US Cloud VM IP Blocks).
"""

import ccxt
import pandas as pd
import numpy as np
import logging

def calculate_orderflow_delta(exchange, symbol="BTC/USD"):
    try:
        # Fetch recent public trades to calculate Cumulative Volume Delta (CVD)
        trades = exchange.fetch_trades(symbol, limit=200)
        buy_vol = sum([t['amount'] for t in trades if t['side'] == 'buy'])
        sell_vol = sum([t['amount'] for t in trades if t['side'] == 'sell'])
        
        total_vol = buy_vol + sell_vol
        if total_vol == 0:
            return None
            
        # Delta Ratio: +1.0 (100% Buys) to -1.0 (100% Sells)
        delta_ratio = (buy_vol - sell_vol) / total_vol
        
        # Fetch Top 10 Order Book Depth for Imbalance Ratio
        orderbook = exchange.fetch_order_book(symbol, limit=10)
        bid_depth = sum([b[1] for b in orderbook['bids']])
        ask_depth = sum([a[1] for a in orderbook['asks']])
        
        total_depth = bid_depth + ask_depth
        imbalance_ratio = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0
        
        return {
            'symbol': symbol,
            'buy_vol': round(buy_vol, 2),
            'sell_vol': round(sell_vol, 2),
            'delta_ratio': round(delta_ratio, 3),
            'imbalance_ratio': round(imbalance_ratio, 3),
            'institutional_short_signal': (delta_ratio < -0.35 and imbalance_ratio < -0.25),
            'institutional_long_signal': (delta_ratio > 0.35 and imbalance_ratio > 0.25)
        }
    except Exception as e:
        print(f"OrderFlow Delta Error: {e}")
        return None

if __name__ == "__main__":
    # Use Coinbase CCXT instance (Cloud VM Friendly)
    exchange = ccxt.coinbase({
        'enableRateLimit': True,
    })
    res = calculate_orderflow_delta(exchange, "BTC/USD")
    print(f"🔬 BTC Institutional Order Flow Analysis:\n{res}")
