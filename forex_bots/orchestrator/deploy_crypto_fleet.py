
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
from datetime import datetime
import numpy as np
import alpaca_trade_api as tradeapi

from crypto_regime_router import classify_regime

# Logging Config
LOG_FILE = '/home/moonrider1968/orchestrator/logs/crypto_fleet.log'
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

# Extract Alpaca Credentials
STOCK_SCRIPT = '/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py'
API_KEY = API_SECRET = None

if os.path.exists(STOCK_SCRIPT):
    with open(STOCK_SCRIPT, 'r') as f:
        for line in f:
            line_str = line.strip()
            if 'API_KEY' in line_str and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not API_KEY: API_KEY = parts[1].strip().strip('"\'')
            if ('SECRET_KEY' in line_str or 'SECRET' in line_str) and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not API_SECRET: API_SECRET = parts[1].strip().strip('"\'')

if not API_KEY or not API_SECRET:
    logging.error("❌ Failed to extract Alpaca API Credentials.")
    sys.exit(1)

api = tradeapi.REST(key_id=API_KEY, secret_key=API_SECRET, base_url='https://paper-api.alpaca.markets', api_version='v2')

CRYPTO_UNIVERSE = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD', 'LTC/USD', 'DOGE/USD', 'BCH/USD', 'UNI/USD', 'DOT/USD', 'AAVE/USD', 'XRP/USD']
MAX_ALLOC_PER_TRADE = 1500.0  # Cap per trade ($)
SL_PCT = 0.015                # -1.5% Hard Stop
TRAIL_ACT_PCT = 0.0075        # +0.75% Early Break-Even Activation
SPREAD_LIMIT = 0.0035         # 0.35% Max Spread Guard

def get_btc_ema20():
    try:
        bars = api.get_crypto_bars('BTC/USD', tradeapi.rest.TimeFrame.Minute, limit=50).df
        if len(bars) >= 20:
            prices = bars['close'].values
            weights = np.exp(np.linspace(-1., 0., 20))
            weights /= weights.sum()
            ema = np.convolve(prices, weights, mode='full')[:len(prices)]
            return prices[-1], ema[-1]
    except Exception as e:
        logging.error(f"Error fetching BTC EMA: {e}")
    return None, None

def run_fleet_scan():
    logging.info("🚀 Starting Modular Crypto Fleet Scan Across 12 Pairs...")
    
    btc_price, btc_ema = get_btc_ema20()
    btc_bullish = (btc_price is not None and btc_ema is not None and btc_price > btc_ema)
    
    positions = {p.symbol: p for p in api.list_positions()}
    
    for symbol in CRYPTO_UNIVERSE:
        clean_symbol = symbol.replace('/', '')
        try:
            # 1. Fetch Price Bars
            bars = api.get_crypto_bars(symbol, tradeapi.rest.TimeFrame.Minute, limit=40).df
            if len(bars) < 30:
                continue
                
            prices = bars['close'].values
            curr_price = prices[-1]
            
            # 2. Robust Spread Check
            try:
                snaps = api.get_crypto_snapshot(symbol)
                snap = None
                if isinstance(snaps, dict) or hasattr(snaps, 'get'):
                    snap = snaps.get(symbol) or snaps.get(clean_symbol)
                    if snap is None and len(snaps) > 0:
                        snap = list(snaps.values())[0]
                else:
                    snap = snaps

                if snap and hasattr(snap, 'latest_quote') and snap.latest_quote:
                    lq = snap.latest_quote
                    bid = float(getattr(lq, 'bid_price', 0) or getattr(lq, 'bp', 0))
                    ask = float(getattr(lq, 'ask_price', 0) or getattr(lq, 'ap', 0))
                    if bid > 0:
                        spread = (ask - bid) / bid
                        if spread > SPREAD_LIMIT:
                            logging.warning(f"⚠️ [SPREAD GUARD] {symbol} spread ({spread:.4f}) > {SPREAD_LIMIT}. Skipping.")
                            continue
            except Exception as se:
                logging.warning(f"⚠️ Spread check skipped for {symbol}: {se}")

            # 3. Regime Routing
            regime, er, h = classify_regime(prices)
            
            # 4. Position Management
            pos = positions.get(symbol) or positions.get(clean_symbol)
            
            if pos:
                entry = float(pos.avg_entry_price)
                pnl_pct = (curr_price - entry) / entry
                
                # Hard Stop Loss
                if pnl_pct <= -SL_PCT:
                    logging.warning(f"🔴 [HARD STOP LOSS] Closing {symbol} at {pnl_pct*100:.2f}% PnL")
                    api.close_position(clean_symbol)
                    continue
                # Trailing Lock-in
                elif pnl_pct >= TRAIL_ACT_PCT:
                    logging.info(f"🟢 [TRAILING LOCK] {symbol} sitting in profit: +{pnl_pct*100:.2f}%")
                    
            else:
                # Entry Logic
                if regime == "TREND" and er >= 0.55:
                    if symbol != 'BTC/USD' and not btc_bullish:
                        logging.info(f"😴 [BTC MACRO GUARD] {symbol} has trend signal (ER: {er:.2f}), but BTC < 20 EMA. Standing down.")
                        continue
                        
                    qty = MAX_ALLOC_PER_TRADE / curr_price
                    logging.info(f"⚡ [EXPERT BREAKOUT ENTRY] Buying {symbol} | Regime: TREND | ER: {er:.2f} | Hurst: {h:.2f}")
                    api.submit_order(symbol=symbol, qty=qty, side='buy', type='market', time_in_force='gtc')
                    
                elif regime == "CHOP":
                    logging.info(f"😴 [REGIME CHOP] {symbol} ER: {er:.2f} | Hurst: {h:.2f}. Standing down.")
                elif regime == "SQUEEZE":
                    logging.info(f"😴 [REGIME SQUEEZE] {symbol} ER: {er:.2f} | Hurst: {h:.2f}. Standing down.")
                    
        except Exception as e:
            logging.error(f"Error scanning {symbol}: {e}")

if __name__ == "__main__":
    while True:
        run_fleet_scan()
        time.sleep(20)
