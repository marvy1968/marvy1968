#!/usr/bin/env python3
"""
SUB-SYSTEM #1: CRYPTO STAGE 4 SHORT DAEMON (STAIR-STEP TP + 0.5% SIZING)
------------------------------------------------------------------------
- Base Sizing: 0.5% NAV per position
- Stair-Stepped Take-Profit Targets (TP1: 1.5x ATR, TP2: 2.5x ATR, TP3: 3.5x ATR)
- Scans 30 Binance Perpetual Pairs with Isolated Margin
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

sys.path.append("/home/moonrider1968/bots/crypto_fleet")
from telegram_notifier import notify_trade_execution

LOG_FILE = "/home/moonrider1968/bots/crypto_fleet/logs/stage4_short.log"
STATE_FILE = "/home/moonrider1968/bots/crypto_fleet/crypto_regime_state.json"

MAX_OPEN_POSITIONS = 25

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

CRYPTO_UNIVERSE = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "AVAX/USDT:USDT",
    "NEAR/USDT:USDT", "SUI/USDT:USDT", "ADA/USDT:USDT", "DOT/USDT:USDT",
    "TRX/USDT:USDT", "TON/USDT:USDT", "LINK/USDT:USDT", "UNI/USDT:USDT",
    "AAVE/USDT:USDT", "INJ/USDT:USDT", "TIA/USDT:USDT", "PENDLE/USDT:USDT",
    "APT/USDT:USDT", "ARBITRUM/USDT:USDT", "OP/USDT:USDT", "IMX/USDT:USDT",
    "SEI/USDT:USDT", "FET/USDT:USDT", "RNDR/USDT:USDT", "TAO/USDT:USDT",
    "WORLDCOIN/USDT:USDT", "DOGE/USDT:USDT", "PEPE/USDT:USDT", "WIF/USDT:USDT",
    "SHIB/USDT:USDT", "BONK/USDT:USDT"
]

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
        return {"allow_short_trend": True, "sizing_multiplier": 1.0, "btc_regime": "UNKNOWN"}

def calculate_indicators(df):
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['vol_sma20'] = df['volume'].rolling(window=20).mean()
    
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    return df

def configure_symbol_margin(symbol):
    try:
        exchange.set_margin_mode('ISOLATED', symbol)
    except:
        pass
    try:
        exchange.set_leverage(5, symbol)
    except:
        pass

def evaluate_stage4_short(symbol, brain_state):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe='3m', limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = calculate_indicators(df)
        
        latest = df.iloc[-1]
        below_200 = latest['close'] < latest['ema200']
        near_20 = abs(latest['close'] - latest['ema20']) / latest['ema20'] <= 0.025
        
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        body_size = abs(latest['close'] - latest['open'])
        is_moderate_tail = (upper_wick >= 0.7 * body_size)
        
        if below_200 and near_20 and is_moderate_tail:
            entry_price = latest['close']
            atr_val = latest['atr'] if not np.isnan(latest['atr']) else (entry_price * 0.02)
            rel_volume = latest['volume'] / latest['vol_sma20'] if latest['vol_sma20'] > 0 else 1.0
            
            # 0.5% MICRO BASE SIZING STEPPED BY TIER VOLUME
            tier = 1
            size_pct = 0.005  # 0.5% Base
            if rel_volume >= 2.0:
                tier = 3
                size_pct = 0.015  # 1.5% Max Conviction Tier
            elif rel_volume >= 1.2:
                tier = 2
                size_pct = 0.010  # 1.0% Strong Tier
                
            return {
                'symbol': symbol,
                'entry': entry_price,
                'atr': atr_val,
                'tier': tier,
                'base_size_pct': size_pct,
                'rel_vol': round(float(rel_volume), 2)
            }
    except Exception as e:
        return None

def execute_short_order(setup):
    try:
        brain_state = get_brain_state()
        sizing_multiplier = brain_state.get("sizing_multiplier", 1.0)
        if sizing_multiplier <= 0:
            return

        configure_symbol_margin(setup['symbol'])

        nav = brain_state.get("account_nav_usdt", 60000.0)
        effective_pct = setup['base_size_pct'] * sizing_multiplier
        target_dollars = nav * effective_pct
        amount = target_dollars / setup['entry']

        stop_price = round(setup['entry'] + (1.25 * setup['atr']), 4)
        
        # STAIR-STEP TAKE PROFIT BOTTLE-NECK TARGETS
        tp1 = round(setup['entry'] - (1.5 * setup['atr']), 4)  # 50% scale out
        tp2 = round(setup['entry'] - (2.5 * setup['atr']), 4)  # 25% scale out
        tp3 = round(setup['entry'] - (3.5 * setup['atr']), 4)  # 25% final target

        limit_entry_price = round(setup['entry'] * 0.999, 4)

        logging.info(f"⚡ STAIR-STEP SHORT [TIER {setup['tier']}]: {setup['symbol']} | Notional: ${target_dollars:.2f} ({effective_pct*100:.2f}% NAV) | Stop: ${stop_price} | TP1: ${tp1} | TP2: ${tp2} | TP3: ${tp3}")
        
        order = exchange.create_order(
            symbol=setup['symbol'],
            type='limit',
            side='sell',
            amount=amount,
            price=limit_entry_price,
            params={
                'timeInForce': 'IOC',
                'stopLoss': {'triggerPrice': stop_price},
                'takeProfit': {'triggerPrice': tp1}
            }
        )
        
        notify_trade_execution(
            fleet_type="Crypto Futures",
            symbol=setup['symbol'],
            side="SHORT",
            tier=setup['tier'],
            notional=target_dollars,
            entry=limit_entry_price,
            stop=stop_price,
            tp=tp1
        )
    except Exception as e:
        logging.error(f"Execution Error {setup['symbol']}: {e}")

def main():
    logging.info("==================================================")
    logging.info("SUB-SYSTEM #1 CRYPTO STAGE 4 SHORT (0.5% STAIR-STEP EDITION) ONLINE")
    logging.info("==================================================")
    while True:
        try:
            brain_state = get_brain_state()
            if brain_state.get("allow_short_trend", False):
                for symbol in CRYPTO_UNIVERSE:
                    setup = evaluate_stage4_short(symbol, brain_state)
                    if setup:
                        execute_short_order(setup)
                    time.sleep(0.15)
            time.sleep(5)
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    main()
