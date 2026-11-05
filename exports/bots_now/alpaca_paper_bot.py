import os
import sys
import time
import datetime
import json
import logging
from zoneinfo import ZoneInfo
import pandas as pd
from dotenv import load_dotenv
import requests

# --- CONFIGURATION & ALPACA CREDENTIALS ---
load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))

PAPER_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"
NY_TZ = ZoneInfo("America/New_York")

SYMBOL = "SPY"
NOTIONAL_SIZE = 10000.0  # Dollar amount per trade in paper mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("alpaca_paper_bot.log"), logging.StreamHandler(sys.stdout)]
)

def get_headers():
    return {
        "APCA-API-KEY-ID": APCA_API_KEY_ID,
        "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY
    }

def get_account():
    r = requests.get(f"{PAPER_URL}/account", headers=get_headers(), timeout=10)
    return r.json() if r.status_code == 200 else None

def get_position(symbol):
    r = requests.get(f"{PAPER_URL}/positions/{symbol}", headers=get_headers(), timeout=10)
    if r.status_code == 200:
        return r.json()
    return None

def submit_order(symbol, side, notional):
    data = {
        "symbol": symbol,
        "notional": str(notional),
        "side": side,
        "type": "market",
        "time_in_force": "day"
    }
    r = requests.post(f"{PAPER_URL}/orders", headers=get_headers(), json=data, timeout=10)
    if r.status_code == 200:
        logging.info(f"✅ Order executed: {side.upper()} ${notional} of {symbol}")
        return r.json()
    else:
        logging.error(f"❌ Order failed: {r.text}")
        return None

def get_latest_bars(symbol):
    headers = get_headers()
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    
    # Request a safe 1-year historical window to guarantee >200 bars for the SMAs
    end_date = datetime.datetime.now(NY_TZ).strftime("%Y-%m-%d")
    start_date = (datetime.datetime.now(NY_TZ) - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
    
    params = {
        "timeframe": "1Day",
        "start": start_date,
        "end": end_date,
        "limit": 1000,
        "adjustment": "split",
        "feed": "sip"
    }
    
    r = requests.get(url, headers=headers, params=params, timeout=15)
    if r.status_code != 200:
        logging.error(f"Data API error: {r.text}")
        return None
        
    bars = r.json().get("bars", [])
    if not bars:
        return None
        
    df = pd.DataFrame(bars)
    df.rename(columns={"t": "time", "c": "close"}, inplace=True)
    df['close'] = df['close'].astype(float)
    return df

def run_strategy_cycle():
    logging.info(f"--- Running Paper Trading Check for {SYMBOL} ---")
    
    acct = get_account()
    if not acct:
        logging.error("Failed to fetch Alpaca account info.")
        return
    logging.info(f"Account Buying Power: ${float(acct.get('buying_power', 0)):,.2f}")

    df = get_latest_bars(SYMBOL)
    if df is None or len(df) < 200:
        logging.error(f"Insufficient bar data retrieved. Got {0 if df is None else len(df)} bars, need at least 200.")
        return

    df['SMA_50'] = df['close'].rolling(50).mean()
    df['SMA_200'] = df['close'].rolling(200).mean()

    current_price = df['close'].iloc[-1]
    fast_ma = df['SMA_50'].iloc[-1]
    slow_ma = df['SMA_200'].iloc[-1]

    logging.info(f"Price: {current_price:.2f} | 50 SMA: {fast_ma:.2f} | 200 SMA: {slow_ma:.2f}")

    should_be_long = fast_ma > slow_ma
    pos = get_position(SYMBOL)
    is_currently_long = pos is not None

    if should_be_long and not is_currently_long:
        logging.info("📈 Signal: Bullish crossover detected. Entering long position.")
        submit_order(SYMBOL, "buy", NOTIONAL_SIZE)
    elif not should_be_long and is_currently_long:
        logging.info("📉 Signal: Bearish crossover detected. Closing position.")
        submit_order(SYMBOL, "sell", float(pos['market_value']))
    else:
        logging.info("⚖️ Position aligns with signal. No action required.")

if __name__ == "__main__":
    logging.info("🚀 Alpaca Paper Trading Bot Initialized.")
    run_strategy_cycle()
    logging.info("✅ Cycle complete.")
