#!/usr/bin/env python3
"""
OANDA JPY Institutional Trend Expert Daemon (v1.1)
Universe: AUD_JPY, USD_JPY
Execution: H1 Closed Candles | 07:00 - 17:00 UTC Window
Risk: $300 Fixed Dollar Risk with Dynamic JPY Quote Sizing
"""

import os
import sys
import time
import json
import datetime
import logging
from pathlib import Path
import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

# Load credentials from parent or local .env
env_paths = [
    Path("/home/moonrider1968/bots/.env"),
    Path("/home/moonrider1968/bots/killer_beast_sniper/.env"),
    Path(__file__).parent / ".env"
]
for ep in env_paths:
    if ep.exists():
        load_dotenv(ep)
        break

OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

STATE_FILE = Path("/home/moonrider1968/bots/oanda_data/oanda_jpy_positions.json")
LOG_FILE = Path("/home/moonrider1968/bots/oanda_data/oanda_expert.log")
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE))
    ]
)

if not OANDA_API_KEY:
    logging.error("❌ FATAL: Missing OANDA_API_KEY in environment or .env file.")
    sys.exit(1)

PAIRS_CONFIG = {
    "AUD_JPY": {"adx_thresh": 30.0, "r_trigger": 1.5, "atr_mult": 0.5, "max_bars": 36, "spread": 1.8, "pip_val": 0.01},
    "USD_JPY": {"adx_thresh": 25.0, "r_trigger": 1.5, "atr_mult": 0.8, "max_bars": 48, "spread": 1.4, "pip_val": 0.01},
}

HEADERS = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}
BASE_RISK_DOLLARS = 300.0

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            logging.warning(f"Failed to parse state file: {e}")
            return {}
    return {}

def save_state(data):
    STATE_FILE.write_text(json.dumps(data, indent=2))

def fetch_h1_candles(pair, count=250):
    url = f"{OANDA_URL}/v3/instruments/{pair}/candles"
    params = {"granularity": "H1", "count": count, "price": "M"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            logging.error(f"Failed to fetch {pair}: HTTP {r.status_code}")
            return None
        candles = r.json().get('candles', [])
        return pd.DataFrame([{
            'time': c['time'],
            'open': float(c['mid']['o']),
            'high': float(c['mid']['h']),
            'low': float(c['mid']['l']),
            'close': float(c['mid']['c']),
            'volume': float(c['volume'])
        } for c in candles if c.get('complete', False)])
    except Exception as e:
        logging.error(f"Error fetching candles for {pair}: {e}")
        return None

def compute_indicators(df):
    df = df.copy()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
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

def run_cycle():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    logging.info(f"=== OANDA JPY EXPERT CYCLE: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')} ===")
    state = load_state()

    for pair, cfg in PAIRS_CONFIG.items():
        df = fetch_h1_candles(pair)
        if df is None or len(df) < 200:
            logging.warning(f"Insufficient candle history for {pair}.")
            continue
        df = compute_indicators(df)
        last = df.iloc[-1]
        pip_val = cfg["pip_val"]

        pos = state.get(pair)
        if pos:
            pos['bars'] += 1
            entry = pos['entry_px']
            risk = pos['initial_risk']
            exit_trade = False
            exit_px = last['close']

            if pos['side'] == 'LONG':
                mfe = last['high'] - entry
                r_mult = mfe / risk
                if r_mult >= 2.0:
                    pos['trail_sl'] = max(pos['trail_sl'], last['ema21'] - (cfg['atr_mult'] * last['atr']))
                elif r_mult >= cfg['r_trigger']:
                    pos['trail_sl'] = max(pos['trail_sl'], entry + (0.5 * risk))

                if last['low'] <= pos['trail_sl']:
                    exit_px = pos['trail_sl']
                    exit_trade = True
                elif pos['bars'] >= cfg['max_bars']:
                    exit_trade = True
            else:
                mfe = entry - last['low']
                r_mult = mfe / risk
                if r_mult >= 2.0:
                    pos['trail_sl'] = min(pos['trail_sl'], last['ema21'] + (cfg['atr_mult'] * last['atr']))
                elif r_mult >= cfg['r_trigger']:
                    pos['trail_sl'] = min(pos['trail_sl'], entry - (0.5 * risk))

                if last['high'] >= pos['trail_sl']:
                    exit_px = pos['trail_sl']
                    exit_trade = True
                elif pos['bars'] >= cfg['max_bars']:
                    exit_trade = True

            if exit_trade:
                pips = ((exit_px - entry) / pip_val) if pos['side'] == 'LONG' else ((entry - exit_px) / pip_val)
                pnl = (pips - cfg['spread']) * pip_val * pos['units'] * (1.0 / exit_px)
                logging.info(f"🛑 EXIT {pair} {pos['side']} at {exit_px:.3f} | Bars: {pos['bars']} | PnL: ${pnl:+,.2f}")
                del state[pair]
            else:
                state[pair] = pos
                logging.info(f"📊 ACTIVE {pair} {pos['side']} | Px: {last['close']:.3f} | SL: {pos['trail_sl']:.3f} | Bars: {pos['bars']}")

        else:
            in_session = (7 <= now_utc.hour <= 17)
            logging.info(f"🔍 SCAN {pair} | ADX: {last['adx']:.1f} (req: {cfg['adx_thresh']}) | Session (07-17 UTC): {in_session}")
            if in_session and last['adx'] >= cfg['adx_thresh']:
                stop_dist = max(20.0 * pip_val, 1.5 * last['atr'])
                risk_per_unit = stop_dist * (1.0 / last['close'])
                units = min(max(int(BASE_RISK_DOLLARS / risk_per_unit), 5000), 100000)

                if last['close'] > last['sma200'] and last['ema21'] > last['sma50']:
                    if last['low'] <= last['ema21'] and last['close'] > last['ema21']:
                        state[pair] = {'side': 'LONG', 'entry_px': last['close'], 'trail_sl': last['close'] - stop_dist, 'initial_risk': stop_dist, 'units': units, 'bars': 0}
                        logging.info(f"🟢 ENTRY LONG {pair} at {last['close']:.3f} | Units: {units:,} | SL: {last['close'] - stop_dist:.3f}")
                elif last['close'] < last['sma200'] and last['ema21'] < last['sma50']:
                    if last['high'] >= last['ema21'] and last['close'] < last['ema21']:
                        state[pair] = {'side': 'SHORT', 'entry_px': last['close'], 'trail_sl': last['close'] + stop_dist, 'initial_risk': stop_dist, 'units': units, 'bars': 0}
                        logging.info(f"🔴 ENTRY SHORT {pair} at {last['close']:.3f} | Units: {units:,} | SL: {last['close'] + stop_dist:.3f}")

    save_state(state)
    logging.info("=== CYCLE COMPLETE ===")

if __name__ == "__main__":
    run_cycle()
