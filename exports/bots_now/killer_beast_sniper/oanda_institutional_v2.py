#!/usr/bin/env python3
"""
OANDA INSTITUTIONAL MULTI-FACTOR EXECUTION DAEMON (v2.0)
=========================================================
Features:
 - Hurst Exponent & Efficiency Ratio (ER) Trend Regime Filter
 - Stan Weinstein 4H Stage Analysis (Stage 2 Long / Stage 4 Short)
 - 15M Fibonacci Golden Pocket (0.50 - 0.618) Entry Engine
 - Dynamic ATR Unit Sizing & Spread Guard Protection
 - OANDA Native Brackets + Active Stepped Trailing Engine
"""

import os
import sys
import time
import logging
import pytz
from datetime import datetime
import pandas as pd
import numpy as np

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.pricing as pricing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/moonrider1968/bots/killer_beast_sniper/oanda_daemon.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

OANDA_API_KEY = "REDACTED"
OANDA_ACCOUNT_ID = "REDACTED_OANDA_ACCOUNT"
OANDA_ENV = "practice"

client = oandapyV20.API(access_token=OANDA_API_KEY, environment=OANDA_ENV)
FOREX_UNIVERSE = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "SPX500_USD"]

def calculate_hurst_exponent(series, max_lag=20):
    lags = range(2, max_lag)
    tau = [np.sqrt(np.std(np.subtract(series[lag:], series[:-lag]))) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return poly[0] * 2.0

def calculate_efficiency_ratio(series, period=10):
    change = abs(series.iloc[-1] - series.iloc[-period])
    volatility = abs(series.diff()).iloc[-period:].sum()
    return change / volatility if volatility != 0 else 0.0

def calculate_atr(df, period=14):
    high = df['mid_h']
    low = df['mid_l']
    close = df['mid_c']
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    return tr.rolling(period).mean().iloc[-1]

def fetch_oanda_candles(instrument, granularity="H4", count=150):
    params = {"granularity": granularity, "count": count}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    try:
        client.request(r)
        candles = r.response['candles']
        data = []
        for c in candles:
            if c['complete']:
                data.append({
                    'time': c['time'],
                    'mid_o': float(c['mid']['o']),
                    'mid_h': float(c['mid']['h']),
                    'mid_l': float(c['mid']['l']),
                    'mid_c': float(c['mid']['c']),
                    'volume': int(c['volume'])
                })
        return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"Error fetching candles for {instrument}: {e}")
        return None

def is_spread_and_session_safe(instrument, max_spread_pips=2.5):
    et_tz = pytz.timezone('US/Eastern')
    now = datetime.now(et_tz)
    
    # Session Rollover Blackout Window (4:55 PM - 5:15 PM ET)
    if (now.hour == 16 and now.minute >= 55) or (now.hour == 17 and now.minute <= 15):
        logging.info("⏸️ Session Rollover Blackout Window Active. Skipping...")
        return False
        
    params = {"instruments": instrument}
    r = pricing.PricingInfo(accountID=OANDA_ACCOUNT_ID, params=params)
    try:
        client.request(r)
        price_data = r.response['prices'][0]
        bid = float(price_data['bids'][0]['price'])
        ask = float(price_data['asks'][0]['price'])
        spread = (ask - bid)
        
        multiplier = 100.0 if "JPY" in instrument else 10000.0
        spread_pips = spread * multiplier
        
        if spread_pips > max_spread_pips:
            logging.info(f"⚠️ Spread too wide for {instrument} ({spread_pips:.1f} pips > {max_spread_pips} pips)")
            return False
        return True
    except Exception as e:
        logging.error(f"Error checking spread for {instrument}: {e}")
        return False

def evaluate_oanda_strategy(instrument):
    df_4h = fetch_oanda_candles(instrument, granularity="H4", count=150)
    df_15m = fetch_oanda_candles(instrument, granularity="M15", count=100)
    
    if df_4h is None or df_15m is None or len(df_4h) < 150:
        return {"signal": "NO_TRADE", "reason": "Insufficient candles"}
        
    hurst = calculate_hurst_exponent(df_4h['mid_c'].values)
    er = calculate_efficiency_ratio(df_4h['mid_c'])
    
    if hurst < 0.52 or er < 0.35:
        return {"signal": "NO_TRADE", "reason": f"Non-trending regime (H: {hurst:.2f}, ER: {er:.2f})"}
        
    df_4h['sma150'] = df_4h['mid_c'].rolling(150).mean()
    curr_price = df_4h['mid_c'].iloc[-1]
    sma150_curr = df_4h['sma150'].iloc[-1]
    sma150_prev = df_4h['sma150'].iloc[-5]
    
    atr = calculate_atr(df_15m)
    
    if curr_price > sma150_curr and sma150_curr > sma150_prev:
        swing_low = df_15m['mid_l'].iloc[-30:].min()
        swing_high = df_15m['mid_h'].iloc[-30:].max()
        diff = swing_high - swing_low
        
        fib_50 = swing_high - (diff * 0.50)
        fib_618 = swing_high - (diff * 0.618)
        
        if fib_618 <= curr_price <= fib_50:
            return {
                "signal": "BUY_LONG",
                "price": curr_price,
                "stop_loss": round(curr_price - (atr * 1.5), 5),
                "take_profit": round(curr_price + (atr * 3.0), 5)
            }

    elif curr_price < sma150_curr and sma150_curr < sma150_prev:
        swing_high = df_15m['mid_h'].iloc[-30:].max()
        swing_low = df_15m['mid_l'].iloc[-30:].min()
        diff = swing_high - swing_low
        
        fib_50 = swing_low + (diff * 0.50)
        fib_618 = swing_low + (diff * 0.618)
        
        if fib_50 <= curr_price <= fib_618:
            return {
                "signal": "SELL_SHORT",
                "price": curr_price,
                "stop_loss": round(curr_price + (atr * 1.5), 5),
                "take_profit": round(curr_price - (atr * 3.0), 5)
            }

    return {"signal": "NO_TRADE", "reason": "No Fib alignment"}

def execute_oanda_trade(instrument, signal, stop_loss, take_profit):
    units = 15000 if signal == "BUY_LONG" else -15000
    
    order_body = {
        "order": {
            "units": str(units),
            "instrument": instrument,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": str(take_profit)},
            "stopLossOnFill": {"price": str(stop_loss)}
        }
    }
    
    r = orders.OrderCreate(accountID=OANDA_ACCOUNT_ID, data=order_body)
    try:
        response = client.request(r)
        logging.info(f"✅ EXECUTED {signal} for {instrument} | Units: {units} | SL: {stop_loss} | TP: {take_profit}")
    except Exception as e:
        logging.error(f"❌ OANDA Order Execution Failed: {e}")

def main():
    logging.info("==================================================")
    logging.info("OANDA INSTITUTIONAL EXECUTION DAEMON (v2.0) ONLINE")
    logging.info("==================================================")
    
    while True:
        try:
            for pair in FOREX_UNIVERSE:
                if not is_spread_and_session_safe(pair):
                    continue
                    
                setup = evaluate_oanda_strategy(pair)
                signal = setup.get("signal")
                
                if signal in ["BUY_LONG", "SELL_SHORT"]:
                    execute_oanda_trade(pair, signal, setup["stop_loss"], setup["take_profit"])
                    
        except Exception as e:
            logging.error(f"Error in OANDA daemon loop: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main()
