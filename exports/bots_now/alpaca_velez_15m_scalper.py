from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path("/home/moonrider1968/bots/.env"))
#!/usr/bin/env python3
"""
Alpaca Intraday Oliver Velez Scalper (V3 Hardened)
- Universe: NVDA, TSLA, AMD
- Timeframe: 2-Minute Bars (IEX Feed)
- Session Windows: 09:45-11:30 & 14:00-15:30 EST
- Risk/Reward: 1:1.35 R:R with 1.1x ATR Stop Buffer
- Max Hold: 20 Minutes (Hard Time-Stop)
- Sizing: 25% Equity per position (Max 1 concurrent)
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, OrderRequest, StopOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/moonrider1968/bots/velez_scalper.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

API_KEY = os.getenv("ALPACA_API_KEY", "")
API_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
PAPER = True

SYMBOLS = ["NVDA", "TSLA", "AMD"]
ALLOCATION_PCT = 0.25
MAX_HOLD_SECONDS = 1200         # 20 minutes max hold
TARGET_R = 1.35

trading_client = TradingClient(API_KEY, API_SECRET, paper=PAPER)
data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

# OWNERSHIP_V1 (2026-10-01): shared paper account -> this bot only touches orders
# tagged "vs-" and positions recorded in its own ledger (~/bots/ownership/velez_scalper_ledger.json)
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership
OWN = Ownership("velez_scalper", "vs-", key=API_KEY, secret=API_SECRET, logger=logging.getLogger())

active_positions = {}  # {symbol: {"entry_time": datetime, "entry_price": float, "stop_loss": float, "take_profit": float, "qty": int}}

def get_account_equity() -> float:
    account = trading_client.get_account()
    return float(account.equity)

def is_valid_session_window() -> bool:
    # 09:45-11:30 & 14:00-15:30 EST -> 13:45-15:30 & 18:00-19:30 UTC
    now_utc = datetime.now(timezone.utc).time()
    morning = (now_utc >= pd.to_datetime("13:45").time()) and (now_utc <= pd.to_datetime("15:30").time())
    afternoon = (now_utc >= pd.to_datetime("18:00").time()) and (now_utc <= pd.to_datetime("19:30").time())
    return morning or afternoon

def fetch_bars(symbol: str) -> pd.DataFrame:
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    start = now - timedelta(hours=8)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        feed=DataFeed.IEX
    )
    bars = data_client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)
        
    resampled = df.resample("2min").agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()

    resampled['ema20'] = resampled['close'].ewm(span=20, adjust=False).mean()
    resampled['sma200'] = resampled['close'].rolling(window=200, min_periods=20).mean()
    resampled['tr'] = np.maximum(
        resampled['high'] - resampled['low'],
        np.maximum(abs(resampled['high'] - resampled['close'].shift(1)), abs(resampled['low'] - resampled['close'].shift(1)))
    )
    resampled['atr14'] = resampled['tr'].rolling(window=14).mean()
    resampled['avg_range'] = (resampled['high'] - resampled['low']).rolling(window=20).mean()
    resampled['vol_sma'] = resampled['volume'].rolling(window=20).mean()
    resampled['ema_slope'] = resampled['ema20'] - resampled['ema20'].shift(3)
    return resampled.dropna()

def check_velez_signal(df: pd.DataFrame) -> bool:
    if len(df) < 25:
        return False
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    is_bull = (curr['close'] > curr['ema20']) and (curr['ema20'] > curr['sma200'])
    rising_slope = curr['ema_slope'] > (0.05 * curr['atr14'])
    vol_expansion = curr['volume'] >= (1.2 * curr['vol_sma'])
    if not (is_bull and rising_slope and vol_expansion):
        return False

    bar_range = curr['high'] - curr['low']
    is_elephant = (bar_range > 1.8 * curr['avg_range']) and (curr['close'] > curr['open']) and \
                  ((curr['close'] - curr['low']) / (bar_range + 1e-6) > 0.75)
    is_pullback = (prev['low'] <= prev['ema20'] * 1.002) and (curr['close'] > prev['high']) and \
                  (curr['close'] > curr['ema20'])

    return is_elephant or is_pullback

def manage_positions():
    """OWNERSHIP_V1: only time-stop positions this bot opened (vs- ledger)."""
    global active_positions
    now = datetime.now(timezone.utc)
    try:
        owned = OWN.reconcile()
    except Exception as e:
        logging.error(f"Error reconciling own positions: {e}")
        return

    owned_syms = {rec.get("symbol", s) for s, rec in owned.items()}
    for sym in list(active_positions.keys()):
        if sym not in owned_syms:
            logging.info(f"[CLOSED] {sym} no longer held by this bot.")
            del active_positions[sym]

    for s, rec in owned.items():
        sym = rec.get("symbol", s)
        if sym not in active_positions:
            try:
                opened = datetime.fromisoformat(rec.get("opened_at"))
            except Exception:
                opened = now
            active_positions[sym] = {"entry_time": opened, "qty": rec.get("qty", 0)}

        elapsed = (now - active_positions[sym]["entry_time"]).total_seconds()
        if elapsed >= MAX_HOLD_SECONDS:
            logging.warning(f"[TIME-STOP] {sym} held for {int(elapsed)}s. Closing own qty at market.")
            try:
                OWN.safe_exit(sym, reason="20-min time stop")
            except Exception as e:
                logging.error(f"Error closing {sym} on time stop: {e}")

def run_scalper_cycle():
    manage_positions()
    if len(active_positions) >= 2:
        return

    if not is_valid_session_window():
        return

    equity = get_account_equity()
    trade_capital = equity * ALLOCATION_PCT

    for sym in SYMBOLS:
        try:
            bars = fetch_bars(sym)
            if bars.empty:
                continue

            if check_velez_signal(bars):
                curr = bars.iloc[-1]
                entry_price = curr['close']
                qty = int(trade_capital // entry_price)
                if qty < 1:
                    continue

                stop_loss = round(min(curr['low'], entry_price - (1.1 * curr['atr14'])), 2)
                risk = entry_price - stop_loss
                take_profit = round(entry_price + (risk * TARGET_R), 2)

                logging.info(f"[SIGNAL] {sym} setup confirmed. Buying {qty} shares @ ${entry_price:.2f} | SL: ${stop_loss} | TP: ${take_profit}")
                order = OWN.entry({
                    "symbol": sym, "qty": str(qty), "side": "buy",
                    "type": "market", "time_in_force": "gtc"
                }, tag="en", meta={"stop_loss": stop_loss, "take_profit": take_profit})
                if order is None:
                    continue
                active_positions[sym] = {
                    "entry_time": datetime.now(timezone.utc),
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "qty": qty
                }
                logging.info(f"[FILLED] Order {order.get('id')} placed for {sym}.")
                break
        except Exception as e:
            logging.error(f"Cycle error on {sym}: {e}")

if __name__ == "__main__":
    logging.info("Alpaca Velez Intraday Scalper (V3 Hardened) Started.")
    while True:
        try:
            clock = trading_client.get_clock()
            if clock.is_open:
                run_scalper_cycle()
            time.sleep(30)
        except KeyboardInterrupt:
            logging.info("Daemon terminated by user.")
            break
        except Exception as e:
            logging.error(f"Daemon loop exception: {e}")
            time.sleep(15)
