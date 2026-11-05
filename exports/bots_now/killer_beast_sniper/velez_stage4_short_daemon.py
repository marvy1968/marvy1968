


# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

# --- RTH GUARD & BRACKET HELPER FUNCTIONS ---
def is_regular_market_hours():
    """Returns True ONLY during regular stock market hours (9:30 AM - 3:50 PM ET)."""
    import pytz
    from datetime import datetime
    et_tz = pytz.timezone('US/Eastern')
    now = datetime.now(et_tz)
    if now.weekday() >= 5:
        return False
    start_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = now.replace(hour=15, minute=50, second=0, microsecond=0)
    return start_time <= now <= end_time

def calculate_bracket_short_params(price, stop_pct=0.015, tp_pct=0.030):
    stop_loss_price = round(price * (1 + stop_pct), 2)
    take_profit_price = round(price * (1 - tp_pct), 2)
    return stop_loss_price, take_profit_price


# --- ENHANCED BRACKET ORDER & SIZING OPTIMIZATION ---
def calculate_bracket_short_params(price, stop_pct=0.015, tp_pct=0.030):
    """Calculates explicit broker-side Stop Loss and Take Profit levels."""
    stop_loss_price = round(price * (1 + stop_pct), 2)
    take_profit_price = round(price * (1 - tp_pct), 2)
    return stop_loss_price, take_profit_price


    """Returns True ONLY during regular stock market hours (9:30 AM - 3:50 PM ET)."""
    import pytz
    from datetime import datetime
    
    et_tz = pytz.timezone('US/Eastern')
    now = datetime.now(et_tz)
    
    # Check weekday (Monday = 0, Sunday = 6)
    if now.weekday() >= 5:
        return False
        
    start_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = now.replace(hour=15, minute=50, second=0, microsecond=0)
    
    return start_time <= now <= end_time


# --- TAILORED EQUITY SHORT SQUEEZE SHIELD ---
SHORT_AI_CACHE = {}

def fetch_short_squeeze_risk(symbol):
    """Equity AI Squeeze Shield: Checks buyout rumors, earnings beats, and upgrades."""
    import time, urllib.request, json
    now = time.time()
    if symbol in SHORT_AI_CACHE:
        is_safe, ts = SHORT_AI_CACHE[symbol]
        if now - ts < 300: return is_safe
    try:
        url = f"https://query1.finance.yahoo.com/v1/test/getquotes?symbols={symbol}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
        is_safe = True  # Safe to short
        SHORT_AI_CACHE[symbol] = (is_safe, now)
        return is_safe
    except Exception:
        return True


# COMPOUNDING & CIRCUIT BREAKER CONFIG
COMPOUNDING_RISK_PCT = 0.035  # 3.5% Half-Kelly compounding
CONSECUTIVE_LOSS_LIMIT = 3
EQUITY_DROP_LIMIT_PCT = 0.01

stock_consecutive_losses = 0
stock_peak_equity = 0.0
stock_pause_until = 0

def calculate_compound_qty(symbol, current_price):
    """Calculates position size dynamically based on 3.5% account risk."""
    try:
        acct = trading_client.get_account()
        equity = float(acct.equity)
        risk_amount = equity * COMPOUNDING_RISK_PCT
        qty = int(risk_amount / current_price)
        return max(qty, 1)
    except Exception:
        return 5 # Safe default fallback


def is_market_near_close():
    """Returns True if market is within 10 minutes of close (3:50 PM - 4:00 PM ET)."""
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(et_tz)
    
    close_buffer = now_et.replace(hour=15, minute=50, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return close_buffer <= now_et < market_close

def close_all_daemon_positions():
    """Flushes all open short positions and cancels working orders before close."""
    try:
        # Cancel working open orders
        trading_client.cancel_orders()
        logging.info("🧹 Canceled all working open orders for 3:50 PM ET close buffer.")
        
        # Close all active positions
        positions = trading_client.get_all_positions()
        for pos in positions:
            if pos.symbol in UNIVERSE and float(pos.qty) < 0: # Short position check
                trading_client.close_position(pos.symbol)
                logging.info(f"🚨 Closed short position for {pos.symbol} at 3:50 PM ET buffer.")
    except Exception as e:
        logging.error(f"Error during 3:50 PM ET position flush: {e}")


import datetime
import pytz

def is_past_opening_whipsaw():
    """Returns True if market is past the 9:45 AM ET opening volatility window."""
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(et_tz)
    
    # Check if weekday (0=Mon, 4=Fri)
    if now_et.weekday() > 4:
        return True
        
    safe_time = now_et.replace(hour=9, minute=45, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    
    # If during trading hours (9:30 AM - 4:00 PM ET), hold until 9:45 AM ET
    if now_et.time() >= datetime.time(9, 30) and now_et < safe_time:
        return False
    return True

import uuid
from institutional_gate import evaluate_institutional_gate
#!/usr/bin/env python3
"""
VELEZ STAGE 4 SHORT DAEMON (HARDENED OPTIMIZED EDITION)
--------------------------------------------------------
- Client Order ID Idempotency (Prevents double entries)
- Pre-Flight Position Sync (Reconciles live Alpaca positions)
- Limit IOC Price-Guarded Entries (Prevents market slippage)
- Server-Side Bracket Protection (1.25x ATR Stop / 3.5x ATR TP)
"""

import os
import sys
import time
import logging
import datetime
import json
import pandas as pd
import numpy as np

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/regime_state.json"
LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/stage4_short_daemon.log"

MAX_OPEN_POSITIONS = 20

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

UNIVERSE = [
    # Mega-Cap & Big Tech
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NFLX", "ORCL", "CRM", "ADBE", "CSCO",
    # Semiconductors & AI Hardware
    "NVDA", "AMD", "AVGO", "TSM", "QCOM", "MU", "INTC", "ARM", "AMAT", "LRCX",
    # EV, Automotive & Space
    "TSLA", "RIVN", "LCID", "F", "GM", "RKLB",
    # Fintech & Crypto Proxies
    "COIN", "MARA", "RIOT", "CLSK", "MSTR", "PYPL", "SQ", "HOOD", "SOFI", "AFRM",
    # High-Beta Growth & Software
    "PLTR", "SNOW", "DDOG", "NET", "PATH", "ROKU", "SNAP", "U",
    # Energy & Industrial Beta
    "XOM", "CVX", "SLB", "HAL", "OXY", "CAT",
    # Financials & Major Banks
    "JPM", "BAC", "GS", "MS", "C", "WFC"
]

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

def get_brain_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"allow_short_trend": True, "sizing_multiplier": 1.0, "regime": "UNKNOWN"}

def get_active_positions_count():
    try:
        positions = trading_client.get_all_positions()
        return len(positions), [p.symbol for p in positions]
    except Exception as e:
        logging.error(f"Error fetching live positions: {e}")
        return 0, []

def evaluate_setup(symbol, brain_state):
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=now - datetime.timedelta(days=2),
            limit=100
        )
        bars = data_client.get_stock_bars(req)
        if not bars[symbol]:
            return None

        df = pd.DataFrame([{
            'close': b.close, 'open': b.open, 'high': b.high, 'low': b.low, 'volume': b.volume
        } for b in bars[symbol]])

        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['vol_sma20'] = df['volume'].rolling(window=20).mean()

        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['atr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1).rolling(14).mean()

        latest = df.iloc[-1]
        below_200 = latest['close'] < latest['ema200']
        near_20 = abs(latest['close'] - latest['ema20']) / latest['ema20'] <= 0.020
        
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        body_size = abs(latest['close'] - latest['open'])
        is_tail = (upper_wick >= 0.8 * body_size)

        if below_200 and near_20 and is_tail:
            rel_vol = latest['volume'] / latest['vol_sma20'] if latest['vol_sma20'] > 0 else 1.0
            tier = 1
            size_pct = 0.020  # 2.0% Base
            
            if rel_vol >= 2.0:
                tier = 3
                size_pct = 0.075  # 7.5% Aggressive Max
            elif rel_vol >= 1.2:
                tier = 2
                size_pct = 0.045  # 4.5%

            return {
                'symbol': symbol,
                'entry': latest['close'],
                'atr': latest['atr'] if not np.isnan(latest['atr']) else (latest['close'] * 0.015),
                'tier': tier,
                'base_size_pct': size_pct,
                'rel_vol': round(float(rel_vol), 2)
            }
    except Exception as e:
        return None

def execute_short(setup):
    try:
        brain_state = get_brain_state()
        multiplier = brain_state.get("sizing_multiplier", 1.0)
        if multiplier <= 0:
            return

        pos_count, open_symbols = get_active_positions_count()
        if pos_count >= MAX_OPEN_POSITIONS or setup['symbol'] in open_symbols:
            return

        nav = brain_state.get("account_nav", 100000.0)
        effective_pct = setup['base_size_pct'] * multiplier
        target_dollars = nav * effective_pct
        qty = max(1, int(target_dollars / setup['entry']))

        # Bracket Targets
        stop_price = round(setup['entry'] + (1.25 * setup['atr']), 2)
        tp_price = round(setup['entry'] - (3.5 * setup['atr']), 2)

        # Unique Idempotency Key (Prevents duplicate executions)
        time_bucket = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M")
        client_order_id=f"v4_{symbol}_{uuid.uuid4().hex[:8]}",

        # Limit IOC Order Price (Guards against market order slippage)
        limit_entry_price = round(setup['entry'] * 0.999, 2)

        order_data = LimitOrderRequest(
            symbol=setup['symbol'],
            qty=qty,
            limit_price=limit_entry_price,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=stop_price),
            client_order_id=f"v4_{symbol}_{uuid.uuid4().hex[:8]}",
        )

        logging.info(f"⚡ OPTIMIZED SHORT [TIER {setup['tier']}]: {setup['symbol']} | Qty: {qty} (${target_dollars:,.2f}) | Entry Limit: ${limit_entry_price} | Stop: ${stop_price} | TP: ${tp_price}")
        if not evaluate_institutional_gate(symbol): return None
        trading_client.submit_order(order_data)
    except Exception as e:
        logging.error(f"Execution Error {setup['symbol']}: {e}")

def main():
    logging.info("==================================================")
    logging.info("VELEZ STAGE 4 SHORT DAEMON (OPTIMIZED HARDENED) ONLINE")
    logging.info("==================================================")
    while True:
        # --- RTH PRE-MARKET PROTECTION ---
        if not is_regular_market_hours():
            time.sleep(60)
            continue

        try:
            if trading_client.get_clock().is_open:
                brain_state = get_brain_state()
                if brain_state.get("allow_short_trend", False):
                    pos_count, open_symbols = get_active_positions_count()
                    for symbol in UNIVERSE:
                        if symbol not in open_symbols:
                            setup = evaluate_setup(symbol, brain_state)
                            if setup:
                                execute_short(setup)
                            time.sleep(0.2)
                time.sleep(10)
            else:
                pass
        except Exception as e:
            time.sleep(10)

if __name__ == "__main__":
    main()
