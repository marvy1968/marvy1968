
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
import uuid
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Extract working credentials directly from velez_stage4_short_daemon.py
stock_script = '/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py'
API_KEY = None
API_SECRET = None

if os.path.exists(stock_script):
    with open(stock_script, 'r') as f:
        for line in f:
            line_str = line.strip()
            if 'API_KEY' in line_str and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not API_KEY:
                    API_KEY = parts[1].strip().strip('"\'')
            if ('SECRET_KEY' in line_str or 'SECRET' in line_str) and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not API_SECRET:
                    API_SECRET = parts[1].strip().strip('"\'')

import alpaca_trade_api as tradeapi
from preference_engine import TickerPreferenceEngine

# --- PATHS & LOGGING SETUP ---
LOG_FILE = '/home/moonrider1968/bots/killer_beast_sniper/logs/crypto_daemon.log'
STATE_FILE = '/home/moonrider1968/bots/killer_beast_sniper/data/crypto_daemon_state.json'

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('CryptoDaemon')

BASE_URL = 'https://paper-api.alpaca.markets'

if not API_KEY or not API_SECRET:
    logger.error("❌ Failed to extract API_KEY/SECRET_KEY from velez_stage4_short_daemon.py!")
    sys.exit(1)

CRYPTO_UNIVERSE = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD', 'LTC/USD', 'DOGE/USD', 'BCH/USD', 'UNI/USD', 'DOT/USD', 'AAVE/USD', 'XRP/USD']
COMPOUNDING_RISK_PCT = 0.035
MAX_SPREAD_PCT = 0.0035
SCAN_INTERVAL_SEC = 20

# --- RISK MANAGEMENT & SIGNAL FILTER THRESHOLDS ---
STOP_LOSS_PCT = 0.015                # -1.5% Hard Stop Loss
COOLDOWN_MINUTES = 30                # 30-minute lock-out after Stop Loss
TRAILING_STOP_ACTIVATION_PCT = 0.015  # Activates trailing stop at +1.5% gain
TRAILING_STOP_DELTA_PCT = 0.010       # 1.0% pullback from peak triggers trailing exit
MAX_FUNDING_RATE = 0.0003            # +0.03% per interval (Overcrowded Longs)
MIN_ADX_STRENGTH = 22.0              # Minimum ADX Trend-Strength required

api = tradeapi.REST(key_id=API_KEY, secret_key=API_SECRET, base_url=BASE_URL, api_version='v2')
pref_engine = TickerPreferenceEngine()

# --- PERSISTENT STATE MANAGEMENT (DISK I/O) ---
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}, {}
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            
        sl_cooldowns = {}
        now = datetime.now()
        for sym, exp_str in data.get('sl_cooldowns', {}).items():
            exp_dt = datetime.fromisoformat(exp_str)
            if exp_dt > now:
                sl_cooldowns[sym] = exp_dt
                
        peak_prices = data.get('peak_prices', {})
        return sl_cooldowns, peak_prices
    except Exception as e:
        logger.error(f"Error loading state from {STATE_FILE}: {e}")
        return {}, {}

def save_state(sl_cooldowns, peak_prices):
    try:
        cooldowns_iso = {sym: dt.isoformat() for sym, dt in sl_cooldowns.items() if dt > datetime.now()}
        data = {
            'sl_cooldowns': cooldowns_iso,
            'peak_prices': peak_prices
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving state to {STATE_FILE}: {e}")

sl_cooldowns, peak_prices = load_state()

def generate_client_order_id(symbol: str) -> str:
    clean_sym = symbol.replace('/', '')
    return f'c4_{clean_sym}_{uuid.uuid4().hex[:8]}'

def get_crypto_quote(symbol: str):
    try:
        snapshots = api.get_crypto_snapshot(symbol)
        snap = snapshots[symbol] if symbol in snapshots else snapshots
        
        lq = getattr(snap, 'latest_quote', None)
        if lq is None and isinstance(snap, dict):
            lq = snap.get('latest_quote')
            
        if lq:
            bid = float(getattr(lq, 'bid_price', getattr(lq, 'bp', 0)))
            ask = float(getattr(lq, 'ask_price', getattr(lq, 'ap', 0)))
            return bid, ask
        return None, None
    except Exception as e:
        logger.error(f'Error fetching quote for {symbol}: {e}')
        return None, None

def get_binance_funding_rate(symbol: str) -> float:
    try:
        clean_pair = symbol.replace('/', '').replace('USD', 'USDT')
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={clean_pair}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(url, headers=headers, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get('lastFundingRate', 0.0))
    except Exception as e:
        logger.warning(f"Could not fetch funding rate for {symbol}: {e}")
    return 0.0

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    try:
        if df.empty or len(df) < period + 5:
            return 30.0
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        
        df['up'] = df['high'] - df['high'].shift(1)
        df['down'] = df['low'].shift(1) - df['low']
        
        df['+dm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0.0)
        df['-dm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0.0)
        
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        
        tr_smooth = df['tr'].rolling(window=period).sum()
        plus_di = 100 * (df['+dm'].rolling(window=period).sum() / (tr_smooth + 1e-9))
        minus_di = 100 * (df['-dm'].rolling(window=period).sum() / (tr_smooth + 1e-9))
        
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))
        adx = dx.rolling(window=period).mean()
        val = adx.iloc[-1]
        return float(val) if not np.isnan(val) else 30.0
    except Exception as e:
        logger.warning(f"ADX calculation exception: {e}")
        return 30.0

def get_trend_strength_adx(symbol: str) -> float:
    try:
        bars = api.get_crypto_bars(symbol, tradeapi.TimeFrame.Minute, limit=60).df
        if not bars.empty:
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.reset_index(level=0, drop=True)
            if len(bars) >= 30:
                return calculate_adx(bars)
    except Exception as e:
        logger.warning(f"Could not fetch bars for ADX check on {symbol}: {e}")
    return 30.0

def calculate_dynamic_qty(account_equity: float, current_price: float) -> float:
    position_value = account_equity * COMPOUNDING_RISK_PCT
    return round(position_value / current_price, 4)

def run_crypto_daemon():
    global sl_cooldowns, peak_prices
    logger.info(f'🚀 Starting 24/7 Crypto Stage 4 Engine (SL: -{STOP_LOSS_PCT*100}% | Trail Act: +{TRAILING_STOP_ACTIVATION_PCT*100}% | Funding Guard: +{MAX_FUNDING_RATE*100:.3f}% | ADX Min: {MIN_ADX_STRENGTH} | Order Guard: ACTIVE)...')
    
    while True:
        try:
            account = api.get_account()
            equity = float(account.equity if hasattr(account, 'equity') else account['equity'])
            raw_positions = api.list_positions()

            clean_crypto_universe = [s.replace('/', '') for s in CRYPTO_UNIVERSE]
            active_crypto_syms = set()

            # --- 1. EXIT MANAGEMENT (TRAILING STOPS & HARD SL) ---
            for p in raw_positions:
                sym = p.symbol if hasattr(p, 'symbol') else p['symbol']
                if sym in clean_crypto_universe:
                    qty = float(p.qty if hasattr(p, 'qty') else p['qty'])
                    if qty <= 0:
                        continue
                    
                    active_crypto_syms.add(sym)
                    entry_p = float(p.avg_entry_price if hasattr(p, 'avg_entry_price') else p['avg_entry_price'])
                    current_p = float(p.current_price if hasattr(p, 'current_price') else p['current_price'])
                    
                    if entry_p > 0:
                        pnl_pct = (current_p - entry_p) / entry_p
                        
                        prev_peak = peak_prices.get(sym, entry_p)
                        current_peak = max(prev_peak, current_p)
                        peak_prices[sym] = current_peak
                        
                        peak_gain_pct = (current_peak - entry_p) / entry_p
                        drawdown_from_peak = (current_peak - current_p) / current_peak
                        
                        # A. HARD STOP LOSS CHECK
                        if pnl_pct <= -STOP_LOSS_PCT:
                            logger.info(f'🛑 [STOP LOSS TRIGGERED] {sym} | Entry: ${entry_p:.2f} | Current: ${current_p:.2f} | PnL: {pnl_pct*100:.2f}%')
                            try:
                                api.close_position(sym)
                                expiry = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
                                sl_cooldowns[sym] = expiry
                                if sym in peak_prices:
                                    del peak_prices[sym]
                                save_state(sl_cooldowns, peak_prices)
                                logger.info(f'✅ [POSITION CLOSED - SL] {sym} closed at stop. {COOLDOWN_MINUTES}m Cooldown saved to disk (until {expiry.strftime("%H:%M:%S")}).')
                            except Exception as ce:
                                logger.error(f'❌ [CLOSE FAILED - SL] {sym}: {ce}')
                        
                        # B. DYNAMIC TRAILING STOP PROFIT CHECK
                        elif peak_gain_pct >= TRAILING_STOP_ACTIVATION_PCT and drawdown_from_peak >= TRAILING_STOP_DELTA_PCT:
                            logger.info(f'🎯 [TRAILING STOP TRIGGERED] {sym} | Peak: ${current_peak:.2f} (+{peak_gain_pct*100:.2f}%) | Current: ${current_p:.2f} | Locked PnL: +{pnl_pct*100:.2f}%')
                            try:
                                api.close_position(sym)
                                if sym in peak_prices:
                                    del peak_prices[sym]
                                save_state(sl_cooldowns, peak_prices)
                                logger.info(f'✅ [POSITION CLOSED - TRAILING PROFIT] {sym} closed cleanly at trailing stop.')
                            except Exception as ce:
                                logger.error(f'❌ [CLOSE FAILED - TRAILING PROFIT] {sym}: {ce}')

            # Clean stale peak prices
            stale_peaks = [sym for sym in peak_prices if sym not in active_crypto_syms]
            if stale_peaks:
                for sym in stale_peaks:
                    del peak_prices[sym]
                save_state(sl_cooldowns, peak_prices)

            # --- 2. ENTRY SIGNAL SCANNING ---
            positions = [p.symbol if hasattr(p, 'symbol') else p['symbol'] for p in raw_positions]
            
            # Fetch open working orders to avoid order-stacking
            raw_open_orders = api.list_orders(status='open')
            open_order_syms = [o.symbol if hasattr(o, 'symbol') else o['symbol'] for o in raw_open_orders]
            clean_open_order_syms = [s.replace('/', '') for s in open_order_syms]

            now = datetime.now()

            for symbol in CRYPTO_UNIVERSE:
                clean_sym = symbol.replace('/', '')

                # Check Stop-Loss Cooldown Status
                if clean_sym in sl_cooldowns:
                    if now < sl_cooldowns[clean_sym]:
                        remaining = int((sl_cooldowns[clean_sym] - now).total_seconds() / 60)
                        logger.info(f'⏳ [SL COOLDOWN] {symbol} on persistent lock-out ({remaining}m remaining). Skipping scan.')
                        continue
                    else:
                        logger.info(f'🔓 [SL COOLDOWN EXPIRED] {symbol} lock-out ended. Resuming scanning.')
                        del sl_cooldowns[clean_sym]
                        save_state(sl_cooldowns, peak_prices)

                eval_res = pref_engine.evaluate_ticker(symbol)
                if eval_res['status'] == 'BENCHED':
                    logger.info(f'🚫 [BENCHED] Skipping {symbol} (Win Rate: {eval_res["win_rate"]*100:.1f}%)')
                    continue

                # Funding Rate Overcrowding Check
                funding_rate = get_binance_funding_rate(symbol)
                if funding_rate > MAX_FUNDING_RATE:
                    logger.warning(f'⚠️ [FUNDING GUARD] {symbol} funding rate ({funding_rate*100:.3f}%) > max ({MAX_FUNDING_RATE*100:.3f}%). Overcrowded long, skipping.')
                    continue

                # ADX Trend Strength Check
                adx_score = get_trend_strength_adx(symbol)
                if adx_score < MIN_ADX_STRENGTH:
                    logger.info(f'😴 [CHOP GUARD] {symbol} ADX ({adx_score:.1f}) < required ({MIN_ADX_STRENGTH}). Market sideways, standing down.')
                    continue

                bid, ask = get_crypto_quote(symbol)
                if not bid or not ask or bid == 0:
                    continue

                spread_pct = (ask - bid) / bid
                if spread_pct > MAX_SPREAD_PCT:
                    logger.warning(f'⚠️ [SPREAD GUARD] {symbol} spread ({spread_pct:.4f}) > limit ({MAX_SPREAD_PCT}). Standing down.')
                    continue

                current_price = (bid + ask) / 2.0
                qty = calculate_dynamic_qty(equity, current_price)

                # DUAL GUARD: Check both open positions AND working open orders
                if clean_sym in positions:
                    continue

                if clean_sym in clean_open_order_syms or symbol in open_order_syms:
                    logger.info(f'⏳ [ORDER PENDING] {symbol} already has an active working limit order. Standing down.')
                    continue

                client_id = generate_client_order_id(symbol)
                risk_amt = equity * COMPOUNDING_RISK_PCT
                logger.info(f'⚡ [CRYPTO SIGNAL] {symbol} | Price: ${current_price:.2f} | Qty: {qty} (${risk_amt:.2f}) | ADX: {adx_score:.1f} | Funding: {funding_rate*100:.3f}%')

                try:
                    order = api.submit_order(
                        symbol=symbol,
                        qty=qty,
                        side='buy',
                        type='limit',
                        time_in_force='gtc',
                        limit_price=ask,
                        client_order_id=client_id
                    )
                    order_id = getattr(order, 'id', 'Submitted')
                    logger.info(f'✅ [ORDER PLACED] {symbol} BUY Limit @ ${ask:.2f} | Order ID: {order_id} | Client ID: {client_id}')
                except Exception as oe:
                    logger.error(f'❌ [ORDER FAILED] {symbol}: {oe}')

            time.sleep(SCAN_INTERVAL_SEC)
        except Exception as e:
            logger.error(f'Execution Loop Exception: {e}')
            time.sleep(SCAN_INTERVAL_SEC)

if __name__ == '__main__':
    run_crypto_daemon()
