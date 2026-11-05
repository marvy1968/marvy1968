#!/usr/bin/env python3
"""
MAIN BRAIN ROUTER (REGIME MANAGER & VIRTUAL STOP)
------------------------------------------------
1. Determines Market Regime using SPY 15M Data:
   - TRENDING BULL: ADX > 22 and Price > 20 SMA
   - RANGING CHOP:  ADX <= 22
   - TRENDING BEAR: ADX > 22 and Price < 20 SMA
2. Controls Sub-Bot Flags via a shared state file (regime_state.json).
3. Enforces Virtual Daily Portfolio Stop (Max 2% Drawdown).
"""


# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import sys
import time
import json
import logging
import datetime
import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/regime_state.json"
LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/main_brain.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# Max Daily Loss Virtual Stop
MAX_DAILY_LOSS_PCT = 0.02  # 2% Max Equity Loss per day

def get_account_info():
    acct = trading_client.get_account()
    return float(acct.equity), float(acct.last_equity)

def calculate_adx_sma(symbol="SPY"):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=5)
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=start, limit=200)
    bars = data_client.get_stock_bars(req)
    
    df = pd.DataFrame([{
        'close': b.close,
        'high': b.high,
        'low': b.low
    } for b in bars[symbol]])
    
    # 20 SMA
    df['sma20'] = df['close'].rolling(20).mean()
    
    # Simple ADX Approximation (14 Period)
    df['up'] = df['high'] - df['high'].shift(1)
    df['down'] = df['low'].shift(1) - df['low']
    df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
    df['atr'] = df['tr'].rolling(14).mean()
    
    df['pos_dm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0)
    df['neg_dm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0)
    
    df['pos_di'] = 100 * (df['pos_dm'].rolling(14).mean() / df['atr'])
    df['neg_di'] = 100 * (df['neg_dm'].rolling(14).mean() / df['atr'])
    
    df['dx'] = 100 * abs(df['pos_di'] - df['neg_di']) / (df['pos_di'] + df['neg_di'])
    df['adx'] = df['dx'].rolling(14).mean()
    
    latest = df.iloc[-1]
    return latest['adx'], latest['close'], latest['sma20']

def update_regime():
    try:
        equity, last_equity = get_account_info()
        daily_pnl_pct = (equity - last_equity) / last_equity
        
        # 1. Check Virtual Stop Loss Fail-Safe
        if daily_pnl_pct <= -MAX_DAILY_LOSS_PCT:
            logging.warning(f"🚨 VIRTUAL STOP TRIGGERED: Daily Loss {daily_pnl_pct*100:.2f}% Exceeds Limit! HALTING ALL BOTS.")
            state = {
                "regime": "HALTED_SAFETY_STOP",
                "allow_long_trend": False,
                "allow_short_trend": False,
                "allow_dip_rebound": False
            }
            with open(STATE_FILE, "w") as f:
                json.dump(state, f)
            return

        # 2. Evaluate Market Regime
        adx, close, sma20 = calculate_adx_sma("SPY")
        
        if adx > 22 and close > sma20:
            regime = "TRENDING_BULL"
            allow_long = True
            allow_short = False
            allow_dip = False
        elif adx > 22 and close < sma20:
            regime = "TRENDING_BEAR"
            allow_long = False
            allow_short = True
            allow_dip = True
        else:
            regime = "RANGING_CHOP"
            allow_long = False
            allow_short = False
            allow_dip = True

        state = {
            "regime": regime,
            "adx": round(float(adx), 2),
            "allow_long_trend": allow_long,
            "allow_short_trend": allow_short,
            "allow_dip_rebound": allow_dip,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        with open(STATE_FILE, "w") as f:
            json.dump(state, f)

        logging.info(f"🧠 MAIN BRAIN STATE: {regime} | SPY ADX: {adx:.2f} | Longs: {allow_long} | Shorts: {allow_short} | Dips: {allow_dip}")

    except Exception as e:
        logging.error(f"Error updating Main Brain state: {e}")

def main():
    import numpy as np
    logging.info("==================================================")
    logging.info("MAIN BRAIN REGIME ROUTER INITIALIZED")
    logging.info("==================================================")
    
    while True:
        if trading_client.get_clock().is_open:
            update_regime()
            time.sleep(180) # Re-evaluate market state every 3 minutes
        else:
            logging.info("Market Closed. Main Brain Sleeping...")
            time.sleep(900)

if __name__ == "__main__":
    import numpy as np
    main()
