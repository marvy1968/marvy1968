#!/usr/bin/env python3
"""
Alpaca 5-Minute ORB Retest Morning Scalp Daemon
Universe: TSLA (Solo High-Alpha Retest Vehicle)
Active Window: 13:45 - 15:30 UTC (09:45 - 11:30 AM EST) Mon-Fri
Opening Range Window: 13:30 - 13:45 UTC (09:30 - 09:45 AM EST)
Target: +1.4R | Stop: max(0.8x ATR, 0.6% px) | Session Hard Exit at 15:30 UTC
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

# Load Environment Credentials
for env_p in [Path("/home/moonrider1968/bots/.env"), Path("/home/moonrider1968/bots/killer_beast_sniper/.env")]:
    if env_p.exists():
        load_dotenv(env_p)
        break

ALPACA_KEY = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks"

STATE_FILE = Path("/home/moonrider1968/bots/alpaca_data/orb_morning_positions.json")
LOG_FILE = Path("/home/moonrider1968/bots/alpaca_data/orb_morning.log")
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

# OWNERSHIP_V1 (2026-10-01): shared paper account. Every order is tagged "orb-";
# exits only for symbols in this bot's own ledger, capped by the LIVE position.
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership
OWN = Ownership("orb_morning", "orb-", key=ALPACA_KEY, secret=ALPACA_SECRET,
                base_url=ALPACA_BASE_URL, logger=logging.getLogger(), crypto=False)


ORB_UNIVERSE = ["TSLA"]
RISK_PER_TRADE_DOLLARS = 300.0

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_state(data):
    STATE_FILE.write_text(json.dumps(data, indent=2))

def fetch_5m_candles(symbol, limit=80):
    for feed in ["sip", "iex"]:
        params = {"timeframe": "5Min", "limit": limit, "feed": feed, "adjustment": "split"}
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
    logging.warning(f"Failed to fetch 5m bars for {symbol}")
    return None

def compute_indicators(df):
    df = df.copy()
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    return df.dropna().reset_index(drop=True)

def place_alpaca_order(symbol, qty, side):
    """OWNERSHIP_V1: ENTRY order only. Tagged orb-; skipped if the account already
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
            logging.info(f"🚀 ORB ORDER EXECUTED: {side.upper()} {qty} {symbol} ({res.get('client_order_id')})")
            return True
        logging.error(f"❌ ORB ORDER REJECTED/SKIPPED {symbol} (see [OWNERSHIP] log line)")
        return False
    except Exception as e:
        logging.error(f"❌ ORB ORDER EXCEPTION {symbol}: {e}")
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
    today_str = now_utc.strftime('%Y-%m-%d')
    logging.info(f"=== ORB 5M MORNING CYCLE: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')} ===")

    in_trading_window = (now_utc.weekday() < 5) and (
        (now_utc.hour == 13 and now_utc.minute >= 45) or
        (now_utc.hour == 14) or
        (now_utc.hour == 15 and now_utc.minute <= 30)
    )

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
    if state.get("_date") != today_str:
        state = {"_date": today_str, "daily_traded": {}}

    for symbol in ORB_UNIVERSE:
        df = fetch_5m_candles(symbol)
        if df is None or len(df) < 25:
            continue

        df = compute_indicators(df)
        last = df.iloc[-1]
        pos = state.get(symbol)

        # 1. Active Position Management
        if pos:
            pos['bars'] += 1
            entry = pos['entry_px']
            risk = pos['risk_dist']
            exit_trade = False
            exit_px = last['close']
            reason = ""

            if pos['side'] == 'LONG':
                if last['high'] >= entry + (1.4 * risk):
                    exit_px = entry + (1.4 * risk)
                    exit_trade = True
                    reason = "TP (+1.4R)"
                elif last['low'] <= pos['trail_sl']:
                    exit_px = pos['trail_sl']
                    exit_trade = True
                    reason = "SL Hit"
                elif (now_utc.hour >= 15 and now_utc.minute >= 30) or (now_utc.hour > 15):
                    exit_px = last['close']
                    exit_trade = True
                    reason = "11:30 EST Hard Time Exit"
            else:
                if last['low'] <= entry - (1.4 * risk):
                    exit_px = entry - (1.4 * risk)
                    exit_trade = True
                    reason = "TP (+1.4R)"
                elif last['high'] >= pos['trail_sl']:
                    exit_px = pos['trail_sl']
                    exit_trade = True
                    reason = "SL Hit"
                elif (now_utc.hour >= 15 and now_utc.minute >= 30) or (now_utc.hour > 15):
                    exit_px = last['close']
                    exit_trade = True
                    reason = "11:30 EST Hard Time Exit"

            if exit_trade:
                pnl = ((exit_px - entry) * pos['shares']) if pos['side'] == 'LONG' else ((entry - exit_px) * pos['shares'])
                sent = close_own_position(symbol, reason)
                if sent is None:
                    state[symbol] = pos  # broker error -> retry next cycle
                    continue
                logging.info(f"🛑 ORB EXIT {symbol} {pos['side']} at {exit_px:.2f} ({reason}) | PnL: ${pnl:+,.2f}")
                del state[symbol]
            else:
                state[symbol] = pos
                logging.info(f"📊 ORB ACTIVE {symbol} {pos['side']} | Px: {last['close']:.2f} | SL: {pos['trail_sl']:.2f} | Bars: {pos['bars']}")

        # 2. Scanning Mode
        else:
            if state.get("daily_traded", {}).get(symbol):
                continue

            today_bars = df[df['time'].dt.strftime('%Y-%m-%d') == today_str]
            or_bars = today_bars[(today_bars['time'].dt.hour == 13) & (today_bars['time'].dt.minute >= 30) & (today_bars['time'].dt.minute < 45)]

            if len(or_bars) < 3:
                logging.info(f"🔍 SCAN {symbol:<5} | Waiting for 15m Opening Range formation (13:30-13:45 UTC)")
                continue

            or_high = or_bars['high'].max()
            or_low = or_bars['low'].min()

            post_or_bars = today_bars[(today_bars['time'].dt.hour == 13) & (today_bars['time'].dt.minute >= 45) | (today_bars['time'].dt.hour >= 14)]
            has_broken_above = (post_or_bars['high'] > or_high).any() if not post_or_bars.empty else False
            has_broken_below = (post_or_bars['low'] < or_low).any() if not post_or_bars.empty else False

            logging.info(f"🔍 SCAN {symbol:<5} | OR: [{or_low:.2f} - {or_high:.2f}] | Px: {last['close']:.2f} | Window: {in_trading_window}")

            if in_trading_window and (now_utc.hour == 13 or (now_utc.hour == 14 and now_utc.minute <= 45)):
                stop_dist = max(0.8 * last['atr'], last['close'] * 0.006)
                shares = max(int(RISK_PER_TRADE_DOLLARS / stop_dist), 1)

                if last['volume'] >= 1.2 * last['vol_sma20']:
                    if has_broken_above and last['low'] <= or_high * 1.002 and last['close'] > or_high:
                        if place_alpaca_order(symbol, shares, "buy"):
                            state[symbol] = {
                                'side': 'LONG', 'entry_px': last['close'], 'risk_dist': stop_dist,
                                'trail_sl': last['close'] - stop_dist, 'shares': shares, 'bars': 0
                            }
                            state["daily_traded"][symbol] = True
                            logging.info(f"🟢 ORB ENTRY LONG {symbol} at {last['close']:.2f} | Shares: {shares} | SL: {last['close'] - stop_dist:.2f}")

                    elif has_broken_below and last['high'] >= or_low * 0.998 and last['close'] < or_low:
                        if place_alpaca_order(symbol, shares, "sell"):
                            state[symbol] = {
                                'side': 'SHORT', 'entry_px': last['close'], 'risk_dist': stop_dist,
                                'trail_sl': last['close'] + stop_dist, 'shares': shares, 'bars': 0
                            }
                            state["daily_traded"][symbol] = True
                            logging.info(f"🔴 ORB ENTRY SHORT {symbol} at {last['close']:.2f} | Shares: {shares} | SL: {last['close'] + stop_dist:.2f}")

    save_state(state)
    logging.info("=== ORB CYCLE COMPLETE ===")

if __name__ == "__main__":
    run_cycle()
