#!/usr/bin/env python3
"""
Alpaca H1 Multi-Asset Dual-Profile Trend & Hybrid Daemon
Profiles:
  1. TREND_RUNNER: Open-ended ATR trailing stop on high-beta momentum leaders.
  2. HYBRID_TARGET: +1.5% (+1.8R) quick target & 30-bar time exit on range-bound equities.
Universe: Certified 15-Stock Matrix across 11 S&P 500 Sectors
Active Window: 13:00 - 21:00 UTC (09:00 AM - 05:00 PM EST) Mon-Fri
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
ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks"

STATE_FILE = Path("/home/moonrider1968/bots/alpaca_data/stock_h1_positions.json")
LOG_FILE = Path("/home/moonrider1968/bots/alpaca_data/stock_expert.log")
CONFIG_FILE = Path("/home/moonrider1968/bots/stock_h1_certified_config.json")
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

# OWNERSHIP_V1 (2026-10-01): shared paper account. Every order is tagged "se-";
# exits only for symbols in this bot's own ledger, capped by the LIVE position.
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership
OWN = Ownership("stock_expert", "se-", key=ALPACA_KEY, secret=ALPACA_SECRET,
                base_url=ALPACA_BASE_URL, logger=logging.getLogger(), crypto=False)


BASE_RISK_DOLLARS = 300.0

# Certified 15-Stock Matrix with Strategy Profiles
DUAL_PROFILE_CONFIG = {
    # 🏃 PROFILE 1: TREND RUNNERS (Open-ended ATR Trailing Stops)
    "META":  {"profile": "TREND_RUNNER",  "mode": "BOTH",       "adx": 25.0, "r_trig": 2.0, "atr_mult": 1.0, "max_bars": 36},
    "GOOGL": {"profile": "TREND_RUNNER",  "mode": "BOTH",       "adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 48},
    "AVGO":  {"profile": "TREND_RUNNER",  "mode": "BOTH",       "adx": 20.0, "r_trig": 2.0, "atr_mult": 1.4, "max_bars": 36},
    "NEM":   {"profile": "TREND_RUNNER",  "mode": "BOTH",       "adx": 20.0, "r_trig": 1.5, "atr_mult": 1.4, "max_bars": 48},
    "FCX":   {"profile": "TREND_RUNNER",  "mode": "BOTH",       "adx": 25.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 36},
    "UNH":   {"profile": "TREND_RUNNER",  "mode": "BOTH",       "adx": 30.0, "r_trig": 1.5, "atr_mult": 0.6, "max_bars": 36},

    # 🎯 PROFILE 2: HYBRID TARGETS (+1.5% / +1.8R Quick Profit & 30-Bar Cap)
    "TSLA":  {"profile": "HYBRID_TARGET", "mode": "SHORT_ONLY", "adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "MSFT":  {"profile": "HYBRID_TARGET", "mode": "LONG_ONLY",  "adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "DIS":   {"profile": "HYBRID_TARGET", "mode": "BOTH",       "adx": 25.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "BAC":   {"profile": "HYBRID_TARGET", "mode": "BOTH",       "adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "HD":    {"profile": "HYBRID_TARGET", "mode": "LONG_ONLY",  "adx": 25.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "COP":   {"profile": "HYBRID_TARGET", "mode": "LONG_ONLY",  "adx": 25.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "NFLX":  {"profile": "HYBRID_TARGET", "mode": "LONG_ONLY",  "adx": 25.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "LLY":   {"profile": "HYBRID_TARGET", "mode": "LONG_ONLY",  "adx": 25.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30},
    "CEG":   {"profile": "HYBRID_TARGET", "mode": "LONG_ONLY",  "adx": 20.0, "r_trig": 1.5, "atr_mult": 1.0, "max_bars": 30}
}

CONFIG_FILE.write_text(json.dumps(DUAL_PROFILE_CONFIG, indent=2))

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_state(data):
    STATE_FILE.write_text(json.dumps(data, indent=2))

def fetch_h1_candles(symbol, limit=250):
    for feed in ["sip", "iex"]:
        params = {"timeframe": "1Hour", "limit": limit, "feed": feed, "adjustment": "split"}
        try:
            r = requests.get(f"{ALPACA_DATA_URL}/{symbol}/bars", headers=HEADERS, params=params, timeout=12)
            if r.status_code == 200:
                bars = r.json().get("bars", [])
                if bars:
                    df = pd.DataFrame([{
                        'time': b['t'], 'open': float(b['o']), 'high': float(b['h']),
                        'low': float(b['l']), 'close': float(b['c']), 'volume': float(b['v'])
                    } for b in bars])
                    df['time'] = pd.to_datetime(df['time'])
                    return df
        except Exception:
            pass
    logging.warning(f"Failed to fetch H1 bars for {symbol}")
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

def place_alpaca_order(symbol, qty, side):
    """OWNERSHIP_V1: ENTRY order only. Tagged se-; skipped if the account already
    has any position or open order on the symbol."""
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "day"
    }
    try:
        res = OWN.entry(payload, tag="en")
        if res is not None:
            logging.info(f"🚀 ORDER EXECUTED: {side.upper()} {qty} {symbol} ({res.get('client_order_id')})")
            return True
        logging.error(f"❌ ORDER REJECTED/SKIPPED {symbol} (see [OWNERSHIP] log line)")
        return False
    except Exception as e:
        logging.error(f"❌ ORDER EXCEPTION {symbol}: {e}")
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
    logging.info(f"=== STOCK H1 DUAL-PROFILE CYCLE: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')} ===")
    
    in_session = (now_utc.weekday() < 5) and (13 <= now_utc.hour <= 21)
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

    for symbol, cfg in DUAL_PROFILE_CONFIG.items():
        df = fetch_h1_candles(symbol)
        if df is None or len(df) < 50:
            continue

        df = compute_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        pos = state.get(symbol)

        profile = cfg.get("profile", "TREND_RUNNER")
        r_trig = cfg.get("r_trig", 1.5)
        atr_mult = cfg.get("atr_mult", 1.0)
        max_bars = cfg.get("max_bars", 36)
        mode = cfg.get("mode", "BOTH")

        # 1. Manage Active Position
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

                # Hybrid Target Profile Exit (+1.5% or +1.8R)
                if profile == "HYBRID_TARGET" and (last['high'] >= entry * 1.015 or r_mult >= 1.8):
                    exit_px = max(entry * 1.015, entry + (1.8 * risk))
                    exit_trade = True
                    reason = "HYBRID_TP (+1.5% / +1.8R)"

                # Dynamic Trailing Stops
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

                if profile == "HYBRID_TARGET" and (last['low'] <= entry * 0.985 or r_mult >= 1.8):
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
                    elif pos['bars'] >= int(max_bars * 0.6 if profile == "TREND_RUNNER" else max_bars):
                        exit_trade = True
                        reason = f"TIME_CAP ({max_bars} BARS)"

            if exit_trade:
                pnl = ((exit_px - entry) * pos['shares']) if pos['side'] == 'LONG' else ((entry - exit_px) * pos['shares'])
                sent = close_own_position(symbol, reason)
                if sent is None:
                    state[symbol] = pos  # broker error -> retry next cycle
                    continue
                logging.info(f"🛑 EXIT {symbol} {pos['side']} at {exit_px:.2f} ({reason}) | PnL: ${pnl:+,.2f}")
                del state[symbol]
            else:
                state[symbol] = pos
                logging.info(f"📊 ACTIVE {symbol} [{profile}] {pos['side']} | Px: {last['close']:.2f} | SL: {pos['trail_sl']:.2f} | Bars: {pos['bars']}")

        # 2. Scanning Mode
        else:
            logging.info(f"🔍 SCAN {symbol:<5} [{profile:<13}] | ADX: {last['adx']:4.1f} (req: {cfg['adx']}) | Mode: {mode} | Session: {in_session}")

            if in_session and last['adx'] >= cfg['adx']:
                stop_dist = max(last['close'] * 0.012, 1.5 * last['atr'])
                shares = max(int(BASE_RISK_DOLLARS / stop_dist), 1)

                # Long Stage 2 Setup
                if mode in ("LONG_ONLY", "BOTH"):
                    if last['close'] > last['sma200'] and last['sma20'] > last['sma50']:
                        if last['low'] <= last['sma20'] and last['close'] > last['sma20']:
                            if place_alpaca_order(symbol, shares, "buy"):
                                state[symbol] = {
                                    'side': 'LONG', 'profile': profile, 'entry_px': last['close'],
                                    'initial_risk': stop_dist, 'trail_sl': last['close'] - stop_dist,
                                    'shares': shares, 'bars': 0
                                }
                                logging.info(f"🟢 ENTRY LONG {symbol} [{profile}] at {last['close']:.2f} | Shares: {shares} | SL: {last['close'] - stop_dist:.2f}")

                # Short Stage 4 Setup
                if symbol not in state and mode in ("SHORT_ONLY", "BOTH"):
                    if last['close'] < last['sma200'] and last['sma20'] < last['sma50']:
                        if last['high'] >= last['sma20'] and last['close'] < last['sma20']:
                            if place_alpaca_order(symbol, shares, "sell"):
                                state[symbol] = {
                                    'side': 'SHORT', 'profile': profile, 'entry_px': last['close'],
                                    'initial_risk': stop_dist, 'trail_sl': last['close'] + stop_dist,
                                    'shares': shares, 'bars': 0
                                }
                                logging.info(f"🔴 ENTRY SHORT {symbol} [{profile}] at {last['close']:.2f} | Shares: {shares} | SL: {last['close'] + stop_dist:.2f}")

    save_state(state)
    logging.info("=== STOCK H1 DUAL-PROFILE CYCLE COMPLETE ===")

if __name__ == "__main__":
    run_cycle()
