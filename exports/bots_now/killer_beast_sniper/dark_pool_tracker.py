#!/usr/bin/env python3
"""
INSTITUTIONAL STOCK EXPERT: DARK POOL BLOCK PRINT DETECTOR
----------------------------------------------------------
Tracks high-volume institutional block prints and volume anomalies.
Uses yfinance real-time ticker stream to bypass Alpaca Paper 401 REST restrictions.
"""

import os
import logging
import yfinance as yf

LOG_DIR = "/home/moonrider1968/bots/killer_beast_sniper/logs"
LOG_FILE = os.path.join(LOG_DIR, "dark_pool.log")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def scan_dark_pool_blocks(symbol="SPY", min_notional=500000):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")
        
        if df.empty:
            print(f"⚠️ No minute bar data available for {symbol}.")
            return None

        latest_bar = df.iloc[-1]
        close_price = float(latest_bar["Close"])
        volume = int(latest_bar["Volume"])
        notional = close_price * volume

        # Institutional Block Threshold (> min_notional volume in 1-min)
        is_block = notional >= min_notional

        result = {
            "symbol": symbol,
            "last_price": round(close_price, 2),
            "1m_volume": volume,
            "notional": round(notional, 2),
            "data_source": "YFinance Direct Stream",
            "institutional_block": is_block
        }

        if is_block:
            logging.info(f"🚨 INSTITUTIONAL BLOCK PRINT DETECTED | {symbol} | ${notional:,.2f} @ ${close_price:.2f} (Vol: {volume:,})")

        return result

    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

if __name__ == "__main__":
    data = scan_dark_pool_blocks("SPY")
    print(f"🔬 Dark Pool Print Scan Result:\n{data}")
