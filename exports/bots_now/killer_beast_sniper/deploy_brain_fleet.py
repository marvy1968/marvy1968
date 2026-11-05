#!/usr/bin/env python3

# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os, sys, time, json, logging, datetime, pandas as pd, numpy as np
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/regime_state.json"
LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/main_brain.log"

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

peak_daily_pnl_pct = 0.0
BASE_EQUITY = None

def update_regime():
    global peak_daily_pnl_pct, BASE_EQUITY
    try:
        acct = trading_client.get_account()
        equity = float(acct.equity)
        
        if BASE_EQUITY is None:
            BASE_EQUITY = equity
            logging.info(f"🔄 BASELINE RESET: Locked at ${BASE_EQUITY:,.2f}")

        daily_pnl_pct = (equity - BASE_EQUITY) / BASE_EQUITY if BASE_EQUITY > 0 else 0
        
        if daily_pnl_pct > peak_daily_pnl_pct:
            peak_daily_pnl_pct = daily_pnl_pct

        # Dynamic Profit Lock Floor
        if peak_daily_pnl_pct >= 0.03:
            stop_floor_pct = peak_daily_pnl_pct - 0.010
        elif peak_daily_pnl_pct >= 0.02:
            stop_floor_pct = 0.0125
        elif peak_daily_pnl_pct >= 0.01:
            stop_floor_pct = 0.0050
        else:
            stop_floor_pct = -0.020

        if daily_pnl_pct <= stop_floor_pct:
            reason = "PROFIT LOCK" if stop_floor_pct > -0.02 else "HARD LOSS STOP"
            logging.warning(f"🚨 {reason} BREACHED: Current ROI {daily_pnl_pct*100:.2f}% <= Floor {stop_floor_pct*100:.2f}%. Liquidating.")
            
            state = {
                "regime": "HALTED_SAFETY_STOP",
                "allow_long_trend": False,
                "allow_short_trend": False,
                "allow_dip_rebound": False,
                "sizing_multiplier": 0.0,
                "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(STATE_FILE, "w") as f: json.dump(state, f)
            
            try:
                trading_client.cancel_orders()
                trading_client.close_all_positions(cancel_orders=True)
            except Exception as e:
                logging.error(f"Error during liquidation: {e}")
            return

        # Dynamic Sizing Multiplier
        if daily_pnl_pct >= 0:
            sizing_multiplier = 1.0
        elif daily_pnl_pct >= -0.005:
            sizing_multiplier = 0.75
        elif daily_pnl_pct >= -0.010:
            sizing_multiplier = 0.50
        else:
            sizing_multiplier = 0.25

        # Regime Analysis via SPY
        now = datetime.datetime.now(datetime.timezone.utc)
        req = StockBarsRequest(symbol_or_symbols="SPY", timeframe=TimeFrame.Minute, start=now - datetime.timedelta(days=5), limit=200)
        bars = data_client.get_stock_bars(req)["SPY"]
        df = pd.DataFrame([{'close': b.close, 'high': b.high, 'low': b.low} for b in bars])
        
        df['sma20'] = df['close'].rolling(20).mean()
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
        adx, close, sma20 = latest['adx'], latest['close'], latest['sma20']
        
        if adx > 22 and close > sma20:
            regime, allow_long, allow_short, allow_dip = "TRENDING_BULL", True, False, False
        elif adx > 22 and close < sma20:
            regime, allow_long, allow_short, allow_dip = "TRENDING_BEAR", False, True, True
        else:
            regime, allow_long, allow_short, allow_dip = "RANGING_CHOP", False, False, True

        state = {
            "regime": regime,
            "adx": round(float(adx), 2),
            "peak_roi": round(float(peak_daily_pnl_pct * 100), 2),
            "stop_floor_pct": round(float(stop_floor_pct * 100), 2),
            "sizing_multiplier": sizing_multiplier,
            "allow_long_trend": allow_long,
            "allow_short_trend": allow_short,
            "allow_dip_rebound": allow_dip,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(STATE_FILE, "w") as f: json.dump(state, f)
        logging.info(f"🧠 BRAIN: {regime} | Peak ROI: {peak_daily_pnl_pct*100:.2f}% | Floor: {stop_floor_pct*100:.2f}% | Size Mult: {sizing_multiplier}x")

    except Exception as e:
        logging.error(f"Main Brain error: {e}")

if __name__ == "__main__":
    logging.info("==================================================")
    logging.info("MAIN BRAIN REBOOTED (CLEAN TEST RUN)")
    logging.info("==================================================")
    while True:
        if trading_client.get_clock().is_open:
            update_regime()
            time.sleep(30)
        else:
            time.sleep(900)
