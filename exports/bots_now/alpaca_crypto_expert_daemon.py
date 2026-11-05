#!/usr/bin/env python3
"""
Alpaca 24/7 Crypto Hybrid Target Expert Daemon
Universe: BTC/USD, SOL/USD, ADA/USD, DOGE/USD, XRP/USD
Schedule: 24/7 Hourly Execution (:02 past each hour)
Model: Hybrid Target Profile (Stage 2 Longs / Stage 4 Shorts + +1.5% / +1.8R Quick TP + 30-Bar Cap)
"""

import os
import sys
import json
import time
import datetime
import logging
import requests
from pathlib import Path
import pandas as pd
import numpy as np
from dotenv import load_dotenv

for env_p in [Path("/home/moonrider1968/bots/.env"), Path("/home/moonrider1968/bots/killer_beast_sniper/.env")]:
    if env_p.exists():
        load_dotenv(env_p)
        break

ALPACA_KEY = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_CRYPTO_DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"

STATE_FILE = Path("/home/moonrider1968/bots/alpaca_data/crypto_h1_positions.json")
LOG_FILE = Path("/home/moonrider1968/bots/alpaca_data/crypto_expert.log")
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE))
    ]
)

if not ALPACA_KEY or not ALPACA_SECRET:
    logging.error("❌ FATAL: Missing Alpaca API Credentials.")
    sys.exit(1)

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type": "application/json"
}

# OWNERSHIP_V1 (2026-10-01): shared paper account. Every order is tagged "ce-";
# exits only for symbols in this bot's own ledger, capped by the LIVE position.
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership
OWN = Ownership("crypto_expert", "ce-", key=ALPACA_KEY, secret=ALPACA_SECRET,
                base_url=ALPACA_BASE_URL, logger=logging.getLogger(), crypto=True)


BASE_RISK_DOLLARS = 300.0

# 24/7 Elite Crypto Universe & Certified Hybrid Parameters
CRYPTO_CONFIG = {
    "BTC/USD":  {"adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30, "min_qty": 0.0001, "precision": 4},
    "SOL/USD":  {"adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30, "min_qty": 0.01,   "precision": 2},
    "ADA/USD":  {"adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30, "min_qty": 1.0,    "precision": 1},
    "DOGE/USD": {"adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30, "min_qty": 1.0,    "precision": 1},
    "XRP/USD":  {"adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30, "min_qty": 1.0,    "precision": 1}
}

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_state(data):
    STATE_FILE.write_text(json.dumps(data, indent=2))

def fetch_crypto_bars(symbol, limit=250):
    # Try Alpaca v1beta3 crypto endpoint
    params = {"symbols": symbol, "timeframe": "1Hour", "limit": limit}
    try:
        r = requests.get(ALPACA_CRYPTO_DATA_URL, headers=HEADERS, params=params, timeout=12)
        if r.status_code == 200:
            bars_dict = r.json().get("bars", {})
            bars = bars_dict.get(symbol, [])
            if bars:
                df = pd.DataFrame([{
                    'time': b['t'], 'open': float(b['o']), 'high': float(b['h']),
                    'low': float(b['l']), 'close': float(b['c']), 'volume': float(b['v'])
                } for b in bars])
                df['time'] = pd.to_datetime(df['time'])
                return df
    except Exception:
        pass

    # Fallback to yfinance proxy for testing if needed
    try:
        import yfinance as yf
        yf_sym = symbol.replace("/", "-")
        df_yf = yf.Ticker(yf_sym).history(period="60d", interval="1h")
        if not df_yf.empty:
            df_yf = df_yf.reset_index().rename(columns={'Datetime': 'time', 'Date': 'time', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'})
            return df_yf[['time', 'open', 'high', 'low', 'close', 'volume']]
    except Exception:
        pass

    logging.warning(f"Failed to fetch Crypto bars for {symbol}")
    return None

def compute_indicators(df):
    df = df.copy()
    df['sma20'] = df['close'].rolling(20).mean()
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()

    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()

    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_di = 100.0 * (pd.Series(plus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    minus_di = 100.0 * (pd.Series(minus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    df['adx'] = (100.0 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))).rolling(14).mean()
    return df.dropna().reset_index(drop=True)

def place_alpaca_crypto_order(symbol, qty, side):
    """OWNERSHIP_V1: ENTRY order only. Tagged ce-; skipped if the account already
    has any position or open order on the symbol."""
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "gtc"
    }
    try:
        res = OWN.entry(payload, tag="en")
        if res is not None:
            logging.info(f"🚀 CRYPTO ORDER EXECUTED: {side.upper()} {qty} {symbol} ({res.get('client_order_id')})")
            return True
        logging.error(f"❌ CRYPTO ORDER REJECTED/SKIPPED {symbol} (see [OWNERSHIP] log line)")
        return False
    except Exception as e:
        logging.error(f"❌ CRYPTO ORDER EXCEPTION {symbol}: {e}")
        return False

def close_own_position(symbol, reason):
    """OWNERSHIP_V1: exit via live-position check; qty = min(own, live). Can't flip."""
    try:
        return OWN.safe_exit(symbol, reason=reason)
    except Exception as e:
        logging.error(f"❌ EXIT EXCEPTION {symbol}: {e}")
        return None

def run_cycle():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    logging.info(f"=== ALPACA CRYPTO 24/7 HYBRID CYCLE: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')} ===")
    state = load_state()
    # OWNERSHIP_V1: drop tracked positions that this bot no longer actually holds
    try:
        owned = OWN.reconcile()
        for _sym in [k for k in list(state.keys()) if not k.startswith("_") and k != "daily_traded"]:
            if isinstance(state.get(_sym), dict) and "side" in state[_sym] and _sym.replace("/", "").upper() not in owned:
                logging.warning(f"⚠️ {_sym} no longer held by this bot (closed externally) - dropping from state")
                del state[_sym]
    except Exception as e:
        logging.error(f"Ownership reconcile failed: {e}")

    for symbol, cfg in CRYPTO_CONFIG.items():
        df = fetch_crypto_bars(symbol)
        if df is None or len(df) < 50:
            continue

        df = compute_indicators(df)
        last = df.iloc[-1]
        pos = state.get(symbol)

        r_trig = cfg.get("r_trig", 1.5)
        atr_mult = cfg.get("atr_mult", 1.0)
        max_bars = cfg.get("max_bars", 30)

        # 1. Manage Open Position
        if pos:
            pos['bars'] += 1
            entry = pos['entry_px']
            risk = pos['initial_risk']
            exit_trade = False
            exit_px = last['close']
            reason = ""

            if pos['side'] == 'LONG':
                mfe = last['high'] - entry
                r_mult = mfe / risk

                # Hybrid Quick Target: +1.5% or +1.8R
                if last['high'] >= entry * 1.015 or r_mult >= 1.8:
                    exit_px = max(entry * 1.015, entry + (1.8 * risk))
                    exit_trade = True
                    reason = "HYBRID_TP (+1.5% / +1.8R)"

                if not exit_trade:
                    if r_mult >= 2.0:
                        pos['trail_sl'] = max(pos['trail_sl'], last['sma20'] - (atr_mult * last['atr']))
                    elif r_mult >= r_trig:
                        pos['trail_sl'] = max(pos['trail_sl'], entry + (0.5 * risk))

                    if last['low'] <= pos['trail_sl']:
                        exit_px = pos['trail_sl']
                        exit_trade = True
                        reason = "SL_HIT"
                    elif pos['bars'] >= max_bars:
                        exit_trade = True
                        reason = f"TIME_CAP ({max_bars} BARS)"

            else:  # SHORT
                mfe = entry - last['low']
                r_mult = mfe / risk

                if last['low'] <= entry * 0.985 or r_mult >= 1.8:
                    exit_px = min(entry * 0.985, entry - (1.8 * risk))
                    exit_trade = True
                    reason = "HYBRID_TP (-1.5% / +1.8R)"

                if not exit_trade:
                    if r_mult >= 2.0:
                        pos['trail_sl'] = min(pos['trail_sl'], last['sma20'] + (atr_mult * 0.6 * last['atr']))
                    elif r_mult >= r_trig:
                        pos['trail_sl'] = min(pos['trail_sl'], entry - (0.5 * risk))

                    if last['high'] >= pos['trail_sl']:
                        exit_px = pos['trail_sl']
                        exit_trade = True
                        reason = "SL_HIT"
                    elif pos['bars'] >= max_bars:
                        exit_trade = True
                        reason = f"TIME_CAP ({max_bars} BARS)"

            if exit_trade:
                pnl = ((exit_px - entry) * pos['qty']) if pos['side'] == 'LONG' else ((entry - exit_px) * pos['qty'])
                sent = close_own_position(symbol, reason)
                if sent is None:
                    state[symbol] = pos  # broker error -> retry next cycle
                    continue
                logging.info(f"🛑 CRYPTO EXIT {symbol} {pos['side']} at {exit_px:.4f} ({reason}) | PnL: ${pnl:+,.2f}")
                del state[symbol]
            else:
                state[symbol] = pos
                logging.info(f"📊 CRYPTO ACTIVE {symbol} {pos['side']} | Px: {last['close']:.4f} | SL: {pos['trail_sl']:.4f} | Bars: {pos['bars']}")

        # 2. Scanning Mode
        else:
            logging.info(f"🔍 SCAN {symbol:<8} | ADX: {last['adx']:4.1f} (req: {cfg['adx']}) | Px: {last['close']:.4f}")

            if last['adx'] >= cfg['adx']:
                stop_dist = max(last['close'] * 0.015, 1.5 * last['atr'])
                raw_qty = BASE_RISK_DOLLARS / stop_dist
                prec = cfg.get("precision", 2)
                qty = round(raw_qty, prec)

                if qty >= cfg.get("min_qty", 0.001):
                    # Long Stage 2 Setup
                    if last['close'] > last['sma200'] and last['sma20'] > last['sma50']:
                        if last['low'] <= last['sma20'] and last['close'] > last['sma20']:
                            if place_alpaca_crypto_order(symbol, qty, "buy"):
                                state[symbol] = {
                                    'side': 'LONG', 'entry_px': last['close'], 'qty': qty,
                                    'initial_risk': stop_dist, 'trail_sl': last['close'] - stop_dist, 'bars': 0
                                }
                                logging.info(f"🟢 CRYPTO ENTRY LONG {symbol} at {last['close']:.4f} | Qty: {qty} | SL: {last['close'] - stop_dist:.4f}")

                    # Short Stage 4 Setup
                    elif last['close'] < last['sma200'] and last['sma20'] < last['sma50']:
                        if last['high'] >= last['sma20'] and last['close'] < last['sma20']:
                            if place_alpaca_crypto_order(symbol, qty, "sell"):
                                state[symbol] = {
                                    'side': 'SHORT', 'entry_px': last['close'], 'qty': qty,
                                    'initial_risk': stop_dist, 'trail_sl': last['close'] + stop_dist, 'bars': 0
                                }
                                logging.info(f"🔴 CRYPTO ENTRY SHORT {symbol} at {last['close']:.4f} | Qty: {qty} | SL: {last['close'] + stop_dist:.4f}")

    save_state(state)
    logging.info("=== ALPACA CRYPTO CYCLE COMPLETE ===")

if __name__ == "__main__":
    run_cycle()
