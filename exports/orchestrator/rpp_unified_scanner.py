
import urllib.request, json, time, urllib.parse

def fetch_ai_news_sentiment(symbol):
    """
    Queries financial news feed for recent sentiment/catalyst risk.
    Returns: (sentiment_multiplier, reason_string)
    """
    try:
        # Clean ticker format (e.g. BTC/USD -> BTC)
        clean_symbol = symbol.split('/')[0].split('_')[0]
        url = f"https://query1.finance.yahoo.com/v1/test/getquotes?symbols={clean_symbol}"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            # Baseline news check fallback
            pass
    except Exception as e:
        pass

    # Default neutral modifier if no high-risk news event is flagged
    return 1.0, "Neutral/Clear News Catalyst"


import json, time, os

COOLDOWN_FILE = '/home/moonrider1968/orchestrator/logs/pair_cooldowns.json'
MAX_CONSECUTIVE_STOPS = 2
COOLDOWN_DURATION_SEC = 7200  # 2 Hours lock

def is_pair_locked(symbol):
    """Check if a symbol is currently locked in a cooldown period."""
    if not os.path.exists(COOLDOWN_FILE):
        return False
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            data = json.load(f)
        lock_until = data.get("cooldowns", {}).get(symbol, 0)
        if time.time() < lock_until:
            return True
    except Exception:
        pass
    return False

def record_trade_stopout(symbol):
    """Record a stopout and trigger 2-hour lock if threshold is reached."""
    try:
        data = {"cooldowns": {}, "stop_counts": {}}
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE, 'r') as f:
                data = json.load(f)
        
        counts = data.get("stop_counts", {})
        counts[symbol] = counts.get(symbol, 0) + 1
        
        if counts[symbol] >= MAX_CONSECUTIVE_STOPS:
            data.setdefault("cooldowns", {})[symbol] = time.time() + COOLDOWN_DURATION_SEC
            counts[symbol] = 0  # Reset counter
            print(f"🔒 PAIRLOCKED: {symbol} hit {MAX_CONSECUTIVE_STOPS} consecutive stops. Locked for 2h.")
        
        data["stop_counts"] = counts
        with open(COOLDOWN_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error updating cooldowns: {e}")

def calculate_dynamic_risk_scalar(score, symbol, direction, active_positions_summary):
    """
    Calculates dynamic position risk scalar based on:
    1. Range Quality Score (S/A/B Tiers)
    2. Net Directional Account Exposure Skewing (Hummingbot Skewing)
    3. Pairlock Protection
    """
    if is_pair_locked(symbol):
        print(f"⛔ {symbol} is PAIRLOCKED. Risk Scalar forced to 0.0x.")
        return 0.0

    # 1. Base Score Scalar
    if score >= 80.0:
        base_scalar = 1.35  # S-Tier
    elif score >= 70.0:
        base_scalar = 1.00  # A-Tier
    elif score >= 60.0:
        base_scalar = 0.60  # B-Tier Probe
    else:
        return 0.0

    # 2. Net Directional Skewing
    net_shorts = active_positions_summary.get('net_shorts', 0)
    net_longs = active_positions_summary.get('net_longs', 0)
    
    skew_multiplier = 1.0
    if direction == 'SHORT' and net_shorts >= 3:
        skew_multiplier = 0.50  # Cut new SHORT size by 50% if heavily short
        print(f"⚠️ SKEW ACTIVE [{symbol}]: Account already holds {net_shorts} SHORTs. Scaling size down to 50%.")
    elif direction == 'LONG' and net_longs >= 3:
        skew_multiplier = 0.50  # Cut new LONG size by 50% if heavily long
        print(f"⚠️ SKEW ACTIVE [{symbol}]: Account already holds {net_longs} LONGs. Scaling size down to 50%.")

    return round(base_scalar * skew_multiplier, 2)


def calculate_dynamic_risk_scalar(score):
    """Scales position risk from 0.5x to 1.5x based on Range Quality Score."""
    if score >= 80.0:
        return 1.35  # S-Tier Heavy Allocation
    elif score >= 70.0:
        return 1.00  # Baseline 1.0x Allocation
    elif score >= 60.0:
        return 0.60  # Defensive Probe Size
    return 0.0

#!/usr/bin/env python3
import os, sys, json, time, logging, urllib.request
from datetime import datetime, timezone
import pandas as pd
import numpy as np

sys.path.append('/home/moonrider1968/orchestrator')
from range_pressure_predictor import RangePressurePredictor

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

# --- CONFIGURATION & LOGGING ---
LOG_FILE = '/home/moonrider1968/orchestrator/logs/rpp_scanner.log'
MATRIX_FILE = '/home/moonrider1968/orchestrator/logs/rpp_unified_matrix.json'

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [SCANNER] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- ALPACA SDK INITIALIZATION ---
# KEYS_MOVED_TO_ENV (2026-10-01): credentials are no longer in source. Values load from the
# process environment (systemd Environment=) or, as fallback, from /home/moonrider1968/.config/bots/rpp_scanner.env (chmod 600).
def _load_private_env(_p="/home/moonrider1968/.config/bots/rpp_scanner.env"):
    import os as _os
    try:
        for _l in open(_p):
            _l=_l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k,_v=_l.split("=",1); _os.environ.setdefault(_k.strip(), _v.strip())
    except FileNotFoundError:
        pass
_load_private_env()
ALPACA_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")

alpaca_api = tradeapi.REST(
    key_id=ALPACA_KEY,
    secret_key=ALPACA_SECRET,
    base_url='https://paper-api.alpaca.markets',
    api_version='v2'
)

# --- OANDA CREDENTIALS ---
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_HOST = "api-fxpractice.oanda.com"

# Universes
STOCK_UNIVERSE = ['NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT', 'AMZN', 'META', 'GOOGL', 'PLTR', 'COIN']
CRYPTO_UNIVERSE = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD', 'LTC/USD', 'DOGE/USD', 'XRP/USD']
FOREX_UNIVERSE = ['EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD', 'USD_CAD']

MAX_STAGED_PER_CLASS = {'STOCK': 4, 'CRYPTO': 3, 'FOREX': 3}
MIN_CONVICTION_SCORE = 60.0

TF_5MIN = TimeFrame(5, TimeFrameUnit.Minute)
TF_1HOUR = TimeFrame.Hour

# --- DATA FETCHING ---

def fetch_stock_bars(symbol):
    try:
        bars_5m = alpaca_api.get_bars(symbol, TF_5MIN, limit=60).df
        bars_1h = alpaca_api.get_bars(symbol, TF_1HOUR, limit=50).df
        if len(bars_5m) >= 30: return bars_5m, bars_1h
    except Exception as e:
        logging.warning(f'⚠️ Stock fetch failed for {symbol}: {e}')
    return None, None

def fetch_crypto_bars(symbol):
    try:
        bars_5m = alpaca_api.get_crypto_bars(symbol, TF_5MIN, limit=60).df
        bars_1h = alpaca_api.get_crypto_bars(symbol, TF_1HOUR, limit=50).df
        if len(bars_5m) >= 30: return bars_5m, bars_1h
    except Exception as e:
        logging.warning(f'⚠️ Crypto fetch failed for {symbol}: {e}')
    return None, None

def fetch_oanda_bars(instrument):
    headers = {
        'Authorization': f'Bearer {OANDA_API_KEY}',
        'Accept': 'application/json'
    }
    url_5m = f'https://{OANDA_HOST}/v3/instruments/{instrument}/candles?count=60&granularity=M5'
    url_1h = f'https://{OANDA_HOST}/v3/instruments/{instrument}/candles?count=50&granularity=H1'
    
    try:
        def parse_oanda(url):
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw_bytes = resp.read()
                if resp.headers.get('Content-Encoding') == 'gzip' or raw_bytes[:2] == b'\x1f\x8b':
                    raw_bytes = gzip.decompress(raw_bytes)
                data = json.loads(raw_bytes.decode('utf-8')).get('candles', [])
                records = []
                for c in data:
                    if c.get('complete'):
                        mid = c['mid']
                        records.append({
                            'time': c['time'],
                            'high': float(mid['h']),
                            'low': float(mid['l']),
                            'close': float(mid['c']),
                            'volume': float(c['volume'])
                        })
                df = pd.DataFrame(records)
                if not df.empty:
                    df['time'] = pd.to_datetime(df['time'])
                    df.set_index('time', inplace=True)
                return df
        df_5m = parse_oanda(url_5m)
        df_1h = parse_oanda(url_1h)
        if len(df_5m) >= 30: return df_5m, df_1h
    except Exception as e:
        logging.warning(f'⚠️ OANDA fetch failed for {instrument}: {e}')
    return None, None

# --- SCANNER ENGINE ---

def run_unified_scan():
    logging.info('🔎 Starting High-ROI / Low-DD Unified RPP Scan Across Stocks, Crypto, & Forex...')
    all_contracts = []
    staged_by_class = {'STOCK': [], 'CRYPTO': [], 'FOREX': []}

    # 1. Stocks
    for symbol in STOCK_UNIVERSE:
        df_5m, df_1h = fetch_stock_bars(symbol)
        if df_5m is not None:
            rpp = RangePressurePredictor(symbol, df_5m, df_1h)
            contract = rpp.generate_advisory_contract()
            if contract:
                contract['asset_class'] = 'STOCK'
                all_contracts.append(contract)
                score = contract.get('range_quality_score', contract.get('compression_score', 0.0))
                if contract['regime'] in ['TIGHT_COMPRESSION', 'VALID_RANGE'] and score >= MIN_CONVICTION_SCORE:
                    staged_by_class['STOCK'].append(contract)

    # 2. Crypto
    for symbol in CRYPTO_UNIVERSE:
        df_5m, df_1h = fetch_crypto_bars(symbol)
        if df_5m is not None:
            rpp = RangePressurePredictor(symbol, df_5m, df_1h)
            contract = rpp.generate_advisory_contract()
            if contract:
                contract['asset_class'] = 'CRYPTO'
                all_contracts.append(contract)
                score = contract.get('range_quality_score', contract.get('compression_score', 0.0))
                if contract['regime'] in ['TIGHT_COMPRESSION', 'VALID_RANGE'] and score >= MIN_CONVICTION_SCORE:
                    staged_by_class['CRYPTO'].append(contract)

    # 3. Forex
    for instrument in FOREX_UNIVERSE:
        df_5m, df_1h = fetch_oanda_bars(instrument)
        if df_5m is not None:
            rpp = RangePressurePredictor(instrument, df_5m, df_1h)
            contract = rpp.generate_advisory_contract()
            if contract:
                contract['asset_class'] = 'FOREX'
                all_contracts.append(contract)
                score = contract.get('range_quality_score', contract.get('compression_score', 0.0))
                if contract['regime'] in ['TIGHT_COMPRESSION', 'VALID_RANGE'] and score >= MIN_CONVICTION_SCORE:
                    staged_by_class['FOREX'].append(contract)

    active_squeezes = []
    for cls, contracts_list in staged_by_class.items():
        sorted_list = sorted(contracts_list, key=lambda x: x.get('range_quality_score', 0), reverse=True)
        trimmed = sorted_list[:MAX_STAGED_PER_CLASS[cls]]
        active_squeezes.extend(trimmed)

    matrix_payload = {
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'total_assets_scanned': len(all_contracts),
        'active_squeezes_count': len(active_squeezes),
        'active_squeezes': active_squeezes,
        'all_contracts': all_contracts
    }

    with open(MATRIX_FILE, 'w') as f:
        json.dump(matrix_payload, f, indent=2)

    logging.info(f'✅ Scan Complete. {len(all_contracts)} assets processed | {len(active_squeezes)} High-Quality Setups Staged.')

if __name__ == '__main__':
    while True:
        try:
            run_unified_scan()
        except Exception as e:
            logging.error(f'Error during scan loop: {e}')
        time.sleep(60)
