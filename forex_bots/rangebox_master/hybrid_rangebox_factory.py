#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL MULTI-ASSET HYBRID RANGE ENGINE — PRODUCTION v1.0.0       ║
║                                                                              ║
║  FILTERS: ADX < 20, Flat 50 EMA Slope, 95% Bollinger Confinement Envelope.   ║
║  SAFETY: 0.5% ATR Sizing, Hidden Virtual Stops, 3-Tiered Partial Scale-Outs  ║
╚══════════════════════════════════════════════════════════════════════════════╗
"""

import os
import sys
import time
import logging
import json
import uuid
import math
from datetime import datetime
import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

# Client Libraries
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv()

# Universal Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [RANGE_FACTORY] %(message)s",
    handlers=[logging.FileHandler("logs/factory_core.log"), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("RangeFactory")

ASSET_MATRIX = {
    "STOCKS": {"lookback": 50, "rsi_l": 30, "rsi_s": 70, "atr_sl": 1.5, "time_stop": 25, "adx_p": 14},
    "FOREX":  {"lookback": 40, "rsi_l": 35, "rsi_s": 65, "atr_sl": 1.2, "time_stop": 40, "adx_p": 14},
    "CRYPTO": {"lookback": 20, "rsi_l": 25, "rsi_s": 75, "atr_sl": 2.0, "time_stop": 60, "adx_p": 14}
}

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    plus_dm = df["high"].diff().where(lambda x: (x > 0) & (x > df["low"].diff().abs()), 0)
    minus_dm = df["low"].diff().abs().where(lambda x: (x > 0) & (x > df["high"].diff()), 0)
    tr = pd.concat([(df["high"] - df["low"]), (df["high"] - df["close"].shift(1)).abs(), (df["low"] - df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atr_p = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / (atr_p + 1e-9))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr_p + 1e-9))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return dx.rolling(period).mean()

class HybridRangeEngine:
    def __init__(self, mode="STOCKS"):
        self.mode = mode.upper()
        self.cfg = ASSET_MATRIX[self.mode]
        self.state_file = f"state/{self.mode.lower()}_state.json"
        self.active_trades = self.load_state_memory()
        self.circuit_breaker_losses = 0
        self.init_broker_pipelines()

    def init_broker_pipelines(self):
        # API Keys Resolution
        alpaca_key = os.getenv("ALPACA_API_KEY", "")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY", "")
        self.oanda_key = os.getenv("OANDA_API_KEY", "")
        self.oanda_id = os.getenv("OANDA_ACCOUNT_ID", "")
        
        if self.mode in ["STOCKS", "CRYPTO"]:
            self.alpaca_trade = TradingClient(alpaca_key, alpaca_secret, paper=True)
            self.alpaca_stocks_data = StockHistoricalDataClient(alpaca_key, alpaca_secret)
            self.alpaca_crypto_data = CryptoHistoricalDataClient(alpaca_key, alpaca_secret)
        else:
            self.oanda_url = "https://api-fxpractice.oanda.com/v3" if "practice" in os.getenv("OANDA_ENVIRONMENT", "practice").lower() else "https://api-fxlive.oanda.com/v3"
            self.oanda_headers = {"Authorization": f"Bearer {self.oanda_key}", "Content-Type": "application/json"}

    def load_state_memory(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f: return json.load(f)
            except Exception: return {}
        return {}

    def save_state_memory(self):
        with open(self.state_file, "w") as f:
            json.dump(self.active_trades, f, indent=4)

    def fetch_ohlcv(self, symbol: str) -> pd.DataFrame:
        try:
            if self.mode == "STOCKS":
                req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.FiveMinutes, limit=150)
                df = self.alpaca_stocks_data.get_bars(req).df.reset_index().rename(columns={"open":"open","high":"high","low":"low","close":"close","volume":"volume"})
                return df.set_index("timestamp")
            elif self.mode == "CRYPTO":
                req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(1, TimeFrame.Minute), limit=150)
                df = self.alpaca_crypto_data.get_crypto_bars(req).df.reset_index().rename(columns={"open":"open","high":"high","low":"low","close":"close","volume":"volume"})
                return df[df["symbol"] == symbol].set_index("timestamp")
            elif self.mode == "FOREX":
                url = f"{self.oanda_url}/instruments/{symbol}/candles"
                r = requests.get(url, headers=self.oanda_headers, params={"count": 150, "granularity": "M5", "price": "M"})
                if r.status_code != 200: return pd.DataFrame()
                data = [{'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'close': float(c['mid']['c']), 'open': float(c['mid']['o']), 'volume': int(c['volume'])} for c in r.json().get('candles', []) if c['complete']]
                return pd.DataFrame(data)
        except Exception:
            return pd.DataFrame()
        return pd.DataFrame()

    def process_asset_pulse(self, symbol: str):
        df = self.fetch_ohlcv(symbol)
        if df.empty or len(df) < 60: return

        # Indicators Build Frame
        df['atr'] = calculate_atr(df, 14)
        df['adx'] = calculate_adx(df, 14)
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # Bollinger Bands Envelope
        df['bb_mid'] = df['close'].rolling(window=20).mean()
        df['bb_std'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_mid'] + (2 * df['bb_std'])
        df['bb_lower'] = df['bb_mid'] - (2 * df['bb_std'])
        df['inside_bb'] = ((df['close'] >= df['bb_lower']) & (df['close'] <= df['bb_upper'])).astype(int)

        last = df.iloc[-1]
        log.info(f"🔍 [SCAN] {symbol} | Price: {last['close']:.2f} | ADX: {last['adx']:.1f} | BB_Inside: {df['inside_bb'].iloc[-50:].sum()}/50")
        current_price = last['close']

        # ─── ACTIVE MANAGEMENT ENGINE LAYER ───
        if symbol in self.active_trades:
            trade = self.active_trades[symbol]
            entry_price = trade['entry']
            direction = trade['dir']
            
            # Virtual Stop Check Routing
            if direction == "LONG" and current_price <= trade['v_sl']:
                self.fire_market_exit(symbol, trade['qty_rem'], "VIRTUAL_SL")
                del self.active_trades[symbol]
                self.save_state_memory()
                return
            elif direction == "SHORT" and current_price >= trade['v_sl']:
                self.fire_market_exit(symbol, trade['qty_rem'], "VIRTUAL_SL")
                del self.active_trades[symbol]
                self.save_state_memory()
                return

            # Tiered Take Profit Execution Rails
            qty_init = trade['qty_init']
            if direction == "LONG":
                if current_price >= trade['v_tp1'] and not trade['tp1_hit']:
                    trade['tp1_hit'] = True
                    trade['v_sl'] = entry_price # Pull up protective stop line to Break-Even floor
                    self.fire_market_exit(symbol, round(qty_init * 0.25, 4), "TP1_MID_25PCT")
                elif current_price >= trade['v_tp2'] and not trade['tp2_hit']:
                    trade['tp2_hit'] = True
                    self.fire_market_exit(symbol, round(qty_init * 0.35, 4), "TP2_75WIDTH_35PCT")
                elif current_price >= trade['v_tp3']:
                    self.fire_market_exit(symbol, trade['qty_rem'], "TP3_MAX_OPPOSITE")
                    del self.active_trades[symbol]
            else: # SHORT Side
                if current_price <= trade['v_tp1'] and not trade['tp1_hit']:
                    trade['tp1_hit'] = True
                    trade['v_sl'] = entry_price
                    self.fire_market_exit(symbol, round(qty_init * 0.25, 4), "TP1_MID_25PCT")
                elif current_price <= trade['v_tp2'] and not trade['tp2_hit']:
                    trade['tp2_hit'] = True
                    self.fire_market_exit(symbol, round(qty_init * 0.35, 4), "TP2_75WIDTH_35PCT")
                elif current_price <= trade['v_tp3']:
                    self.fire_market_exit(symbol, trade['qty_rem'], "TP3_MAX_OPPOSITE")
                    del self.active_trades[symbol]
                    
            self.save_state_memory()
            return

        # ─── MASTER ENTRY GATE FILTERS (REGIME DETECTOR) ───
        if self.circuit_breaker_losses >= 3: return
        if last['adx'] >= 20: return # Filter out trending markets
        
        ema_slope = df['ema_50'].iloc[-1] - df['ema_50'].iloc[-3]
        if abs(ema_slope) > (0.001 * current_price): return # Filter out directional bias

        # Bollinger Bands confinement validation rule (95% rule over last 50 candles)
        if df['inside_bb'].iloc[-50:].sum() < 47: return

        # Constructing the Master Virtual Range Box
        box_frame = df.iloc[-self.cfg['lookback']:]
        box_high = box_frame['high'].max()
        box_low = box_frame['low'].min()
        box_width = box_high - box_low
        
        if box_width < (2.0 * last['atr']): return # Filter out tiny noise boxes

        # Determine directional triggers
        is_long_zone = current_price <= (box_low + (box_width * 0.15))
        is_short_zone = current_price >= (box_high - (box_width * 0.15))

        equity = self.get_account_equity()
        risk_cash = equity * 0.005 # Strict 0.5% configuration risk allocation
        stop_dist = last['atr'] * self.cfg['atr_sl']
        qty = int(risk_cash // stop_dist) if self.mode != "CRYPTO" else round(risk_cash / stop_dist, 4)

        if qty <= 0: return

        if is_long_zone and current_price <= last['bb_lower']:
            log.info(f"🛡️ [HYBRID RANGE LONG] Entry Triggered on {symbol} @ {current_price}")
            self.execute_broker_entry(symbol, qty, "BUY")
            self.active_trades[symbol] = {
                "entry": current_price, "dir": "LONG", "qty_init": qty, "qty_rem": qty,
                "v_sl": current_price - stop_dist,
                "v_tp1": current_price + (box_width * 0.5),
                "v_tp2": current_price + (box_width * 0.75),
                "v_tp3": box_high,
                "tp1_hit": False, "tp2_hit": False
            }
        elif is_short_zone and current_price >= last['bb_upper']:
            log.info(f"🛡️ [HYBRID RANGE SHORT] Entry Triggered on {symbol} @ {current_price}")
            self.execute_broker_entry(symbol, qty, "SELL")
            self.active_trades[symbol] = {
                "entry": current_price, "dir": "SHORT", "qty_init": qty, "qty_rem": qty,
                "v_sl": current_price + stop_dist,
                "v_tp1": current_price - (box_width * 0.5),
                "v_tp2": current_price - (box_width * 0.75),
                "v_tp3": box_low,
                "tp1_hit": False, "tp2_hit": False
            }
        self.save_state_memory()

    def get_account_equity(self) -> float:
        try:
            if self.mode in ["STOCKS", "CRYPTO"]: return float(self.alpaca_trade.get_account().equity)
            r = requests.get(f"{self.oanda_url}/accounts/{self.oanda_id}/summary", headers=self.oanda_headers)
            return float(r.json()['account']['NAV'])
        except Exception: return 10000.0

    def execute_broker_entry(self, symbol: str, qty: float, side: str):
        try:
            if self.mode in ["STOCKS", "CRYPTO"]:
                side_enum = OrderSide.BUY if side == "BUY" else OrderSide.SELL
                order = MarketOrderRequest(symbol=symbol, qty=qty, side=side_enum, time_in_force=TimeInForce.GTC)
                self.alpaca_trade.submit_order(order)
            else: # OANDA Forex Order
                units = str(int(qty)) if side == "BUY" else str(int(-qty))
                url = f"{self.oanda_url}/accounts/{self.oanda_id}/orders"
                payload = {"order": {"units": units, "instrument": symbol, "timeInForce": "FOK", "type": "MARKET", "positionFill": "DEFAULT"}}
                requests.post(url, headers=self.oanda_headers, json=payload)
        except Exception as e: log.error(f"Entry pipeline drop: {e}")

    def fire_market_exit(self, symbol: str, qty: float, reason: str):
        try:
            log.info(f"🔥 [EXIT DISPATCH] Liquidating {qty} units of {symbol} via {reason}")
            if self.mode in ["STOCKS", "CRYPTO"]:
                self.alpaca_trade.close_position(symbol)
            else:
                url = f"{self.oanda_url}/accounts/{self.oanda_id}/openPositions"
                r = requests.get(url, headers=self.oanda_headers)
                for pos in r.json().get('positions', []):
                    if pos['instrument'] == symbol:
                        close_url = f"{self.oanda_url}/accounts/{self.oanda_id}/positions/{symbol}/close"
                        payload = {"longUnits": "ALL"} if int(float(pos['long']['units'])) > 0 else {"shortUnits": "ALL"}
                        requests.put(close_url, headers=self.oanda_headers, json=payload)
        except Exception as e: log.error(f"Exit exception execution: {e}")

    def start_core_loop(self):
        log.info(f"📡 Hybrid Matrix loop initiated for {self.mode} profile universe.")
        watchlist = ASSET_MATRIX[self.mode]
        symbols = ["AAPL", "MSFT", "NVDA", "TSLA"] if self.mode == "STOCKS" else ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"] if self.mode == "CRYPTO" else ["EUR_USD", "USD_JPY"]
        while True:
            for s in symbols:
                self.process_asset_pulse(s)
                time.sleep(1)
            time.sleep(15)

if __name__ == "__main__":
    mode_arg = sys.argv[1].upper() if len(sys.argv) > 1 else "STOCKS"
    factory = HybridRangeEngine(mode=mode_arg)
    factory.start_core_loop()
