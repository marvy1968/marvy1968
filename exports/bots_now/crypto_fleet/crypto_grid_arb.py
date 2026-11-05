#!/usr/bin/env python3
"""
SUB-SYSTEM #5: DYNAMIC ATR GRID ARBITRAGE DAEMON
------------------------------------------------
- Activated during RANGING_CHOP macro regimes
- Places dynamic ATR-spaced buy/sell grid ladders
"""

import os
import sys
import time
import logging
import datetime
import json
import pandas as pd
import numpy as np
import ccxt

LOG_FILE = "/home/moonrider1968/bots/crypto_fleet/logs/grid_arb.log"
STATE_FILE = "/home/moonrider1968/bots/crypto_fleet/crypto_regime_state.json"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

GRID_TARGET = "SOL/USDT:USDT"

exchange = ccxt.binanceusdm({
    'apiKey': 'YOUR_CRYPTO_API_KEY',
    'secret': 'YOUR_CRYPTO_SECRET_KEY',
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def get_brain_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"btc_regime": "UNKNOWN", "sizing_multiplier": 1.0}

def run_grid_cycle():
    brain_state = get_brain_state()
    if brain_state.get("btc_regime") != "RANGING_CHOP" or brain_state.get("sizing_multiplier", 1.0) <= 0:
        return

    bars = exchange.fetch_ohlcv(GRID_TARGET, timeframe='15m', limit=50)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['atr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1).rolling(14).mean()
    
    latest_close = df.iloc[-1]['close']
    latest_atr = df.iloc[-1]['atr']
    grid_step = 0.5 * latest_atr
    
    logging.info(f"📊 [GRID HARVESTER] Target: {GRID_TARGET} | Price: ${latest_close:.2f} | ATR Step: ${grid_step:.2f}")

def main():
    logging.info("==================================================")
    logging.info("SUB-SYSTEM #5 DYNAMIC GRID ARBITRAGE INITIALIZED")
    logging.info("==================================================")
    while True:
        try:
            run_grid_cycle()
            time.sleep(60)
        except Exception as e:
            logging.error(f"Grid loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
