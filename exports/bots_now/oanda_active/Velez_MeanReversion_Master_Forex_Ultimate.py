#!/usr/bin/env python3
"""
Velez Forex Ultimate Beast
- Parameters from .env
- Rate limit protection (HTTP 429 handling)
- Safe historical data fetching (>5000 candles)
"""

import time
import pandas as pd
from datetime import datetime, date, timedelta
import oandapyV20
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.orders import OrderCreate
from oandapyV20.endpoints.accounts import AccountSummary
from oandapyV20.endpoints.positions import PositionClose, OpenPositions
from oandapyV20.exceptions import V20Error
import os
import requests
import csv
import math
from dotenv import load_dotenv

load_dotenv()

# ========================= LOAD FROM .env =========================
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
ACCESS_TOKEN = os.getenv("OANDA_TOKEN", "")
MODE = os.getenv("MODE", "live")

RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.5))
BASE_DAILY_LOSS_LIMIT_PCT = float(os.getenv("BASE_DAILY_LOSS_LIMIT_PCT", 2.0))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", 8.0))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 12))

RSI_LONG_ENTRY = int(os.getenv("RSI_LONG_ENTRY", 28))
RSI_SHORT_ENTRY = int(os.getenv("RSI_SHORT_ENTRY", 72))
BB_MULT_LONG = float(os.getenv("BB_MULT_LONG", 1.01))
BB_MULT_SHORT = float(os.getenv("BB_MULT_SHORT", 0.99))
ADX_MAX = int(os.getenv("ADX_MAX", 22))
STOP_ATR = float(os.getenv("STOP_ATR", 1.5))
TARGET_ATR = float(os.getenv("TARGET_ATR", 2.5))
TRAIL_ATR_MULTIPLIER = float(os.getenv("TRAIL_ATR_MULTIPLIER", 1.2))

TRADE_START_HOUR = int(os.getenv("TRADE_START_HOUR", 3))
TRADE_END_HOUR = int(os.getenv("TRADE_END_HOUR", 22))

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_ENABLED = bool(SLACK_WEBHOOK_URL)

PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "USD_CAD"]
GRANULARITY = "M15"
TRADE_LOG_FILE = "trade_log.csv"

print(f"🚀 Velez Forex Bot starting in {MODE.upper()} mode")
print(f"   RSI: {RSI_LONG_ENTRY}/{RSI_SHORT_ENTRY} | ADX Max: {ADX_MAX} | Risk: {RISK_PER_TRADE_PCT}%")

class VelezForexUltimate:
    def __init__(self):
        self.client = oandapyV20.API(access_token=ACCESS_TOKEN)
        self.open_positions = {}
        self.trailing_stops = {}
        self.equity_history = []
        self.daily_start_balance = None
        self.daily_start_date = None
        self.daily_trades = 0
        self.trading_halted = False
        self.current_daily_loss_pct = 0.0
        self.peak_equity = None
        self.current_drawdown_pct = 0.0
        self._init_trade_log()
        self.sync_positions_from_broker()

    def _init_trade_log(self):
        if not os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "pair", "direction", "entry_price", "fill_price",
                    "stop_loss", "take_profit", "units", "risk_amount_usd",
                    "daily_loss_pct_at_entry", "drawdown_pct_at_entry",
                    "rsi", "adx", "mode", "exit_reason", "exit_price", "pnl"
                ])

    # ==================== RATE LIMIT PROTECTION ====================
    def safe_api_request(self, endpoint):
        """Make API request with automatic rate limit handling (HTTP 429)"""
        for attempt in range(6):
            try:
                return self.client.request(endpoint)
            except V20Error as e:
                if hasattr(e, 'status') and e.status == 429:
                    wait_time = (2 ** attempt) + 1
                    print(f"⚠️ Rate limited by OANDA (429). Waiting {wait_time}s... (attempt {attempt+1}/6)")
                    time.sleep(wait_time)
                else:
                    print(f"❌ API Error: {e}")
                    raise
        raise Exception("Max retries exceeded after OANDA rate limiting")

    # ==================== SAFE LARGE HISTORY FETCHER ====================
    def get_large_history(self, instrument, granularity="M15", days=30):
        """Fetch historical data safely, splitting requests if >5000 candles needed"""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)

        if granularity == "M15":
            candles_per_day = 96
        elif granularity == "M5":
            candles_per_day = 288
        elif granularity == "H1":
            candles_per_day = 24
        else:
            candles_per_day = 96

        total_needed = int(days * candles_per_day)
        chunks = max(1, math.ceil(total_needed / 5000))

        all_candles = []
        current_start = start_time

        print(f"📥 Fetching up to {total_needed} candles for {instrument} ({days} days) in {chunks} chunk(s)...")

        for i in range(chunks):
            chunk_end = min(current_start + timedelta(days=days/chunks), end_time)

            params = {
                "from": current_start.isoformat() + "Z",
                "to": chunk_end.isoformat() + "Z",
                "granularity": granularity,
                "price": "M"
            }

            try:
                r = InstrumentsCandles(instrument=instrument, params=params)
                response = self.safe_api_request(r)
                candles = response.get('candles', [])
                all_candles.extend(candles)
                print(f"   Chunk {i+1}/{chunks}: {len(candles)} candles")
            except Exception as e:
                print(f"   Error on chunk {i+1}: {e}")

            current_start = chunk_end
            time.sleep(0.3)

        df = pd.DataFrame([{
            'time': pd.to_datetime(c['time']),
            'open': float(c['mid']['o']),
            'high': float(c['mid']['h']),
            'low': float(c['mid']['l']),
            'close': float(c['mid']['c']),
        } for c in all_candles])

        df = df.drop_duplicates(subset='time').sort_values('time').reset_index(drop=True)
        print(f"✅ Retrieved {len(df)} candles for {instrument}")
        return df

    def log_trade(self, signal, fill_price=None, units=0, exit_reason=None, exit_price=None, pnl=None):
        try:
            risk_amount = self.get_account_balance() * (RISK_PER_TRADE_PCT / 100)
            with open(TRADE_LOG_FILE, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(), signal['instrument'],
                    signal.get('direction', 'LONG'), signal['entry'],
                    fill_price or signal['entry'], signal['stop'], signal['target'],
                    units, round(risk_amount, 2),
                    round(self.current_daily_loss_pct, 3),
                    round(self.current_drawdown_pct, 3),
                    signal.get('rsi', 0), signal.get('adx', 0), MODE,
                    exit_reason or "", exit_price or "", pnl or ""
                ])
        except Exception as e:
            print(f"Logging error: {e}")

    def send_slack_alert(self, message, level="info"):
        if not SLACK_ENABLED: return
        try:
            payload = {"text": f"📊 *Velez Forex Beast* ({level.upper()})\n{message}"}
            requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        except: pass

    def get_account_balance(self):
        try:
            r = AccountSummary(accountID=ACCOUNT_ID)
            response = self.safe_api_request(r)          # ← Protected
            return float(response['account']['balance'])
        except: return 10000.0

    def sync_positions_from_broker(self):
        try:
            r = OpenPositions(accountID=ACCOUNT_ID)
            response = self.safe_api_request(r)          # ← Protected
            for pos in response.get('positions', []):
                instrument = pos['instrument']
                long_units = int(float(pos.get('long', {}).get('units', 0)))
                short_units = int(float(pos.get('short', {}).get('units', 0)))
                if long_units > 0 or short_units > 0:
                    direction = "LONG" if long_units > 0 else "SHORT"
                    units = long_units if long_units > 0 else short_units
                    print(f"🔄 Synced existing: {direction} {instrument}")
                    self.open_positions[instrument] = {
                        "instrument": instrument, "direction": direction, "units": units,
                        "entry": 0, "stop": 0, "target": 0
                    }
        except Exception as e:
            print(f"Position sync warning: {e}")

    def is_trading_session_active(self):
        hour = datetime.utcnow().hour
        return TRADE_START_HOUR <= hour < TRADE_END_HOUR

    def get_candles(self, instrument, count=300):
        try:
            params = {"count": count, "granularity": GRANULARITY}
            r = InstrumentsCandles(instrument=instrument, params=params)
            response = self.safe_api_request(r)          # ← Protected
            data = response['candles']
            return pd.DataFrame([{
                'time': c['time'],
                'open': float(c['mid']['o']),
                'high': float(c['mid']['h']),
                'low': float(c['mid']['l']),
                'close': float(c['mid']['c']),
            } for c in data])
        except: return pd.DataFrame()

    def rsi(self, series, period=14):
        if series.empty or len(series) < period + 1:
            return pd.Series([50] * len(series))
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def bollinger_bands(self, series, period=20, std=2):
        if series.empty: return {'BBL': pd.Series([0]), 'BBU': pd.Series([0])}
        sma = series.rolling(window=period).mean()
        stddev = series.rolling(window=period).std()
        return {'BBL': sma - stddev * std, 'BBU': sma + stddev * std}

    def adx(self, high, low, close, period=14):
        try:
            if len(high) < period * 2: return 25
            tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
            plus_dm = high - high.shift()
            minus_dm = low.shift() - low
            plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
            minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
            atr = tr.rolling(period).mean()
            plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
            dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
            return dx.rolling(period).mean().iloc[-1]
        except: return 25

    def atr(self, high, low, close, period=14):
        if len(high) < period + 1: return 0.001
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    def update_equity_metrics(self):
        current_balance = self.get_account_balance()
        if self.daily_start_balance is None:
            self.daily_start_balance = current_balance
            self.daily_start_date = date.today()
        daily_loss = self.daily_start_balance - current_balance
        self.current_daily_loss_pct = max(0, (daily_loss / self.daily_start_balance) * 100)
        if self.peak_equity is None:
            self.peak_equity = current_balance
        else:
            self.peak_equity = max(self.peak_equity, current_balance)
        if self.peak_equity > 0:
            self.current_drawdown_pct = ((self.peak_equity - current_balance) / self.peak_equity) * 100
        else:
            self.current_drawdown_pct = 0.0
        return self.current_drawdown_pct

    def get_risk_multiplier(self):
        daily_loss = self.current_daily_loss_pct
        drawdown = self.current_drawdown_pct
        daily_mult = 1.0
        if daily_loss >= BASE_DAILY_LOSS_LIMIT_PCT: daily_mult = 0.0
        elif daily_loss >= SOFT_DAILY_LOSS_WARNING:
            reduction = (daily_loss - SOFT_DAILY_LOSS_WARNING) / (BASE_DAILY_LOSS_LIMIT_PCT - SOFT_DAILY_LOSS_WARNING)
            daily_mult = max(0.2, 1.0 - reduction)
        dd_mult = 1.0
        if drawdown >= MAX_DRAWDOWN_PCT: dd_mult = 0.0
        elif drawdown >= DRAWDOWN_WARNING_PCT:
            reduction = (drawdown - DRAWDOWN_WARNING_PCT) / (MAX_DRAWDOWN_PCT - DRAWDOWN_WARNING_PCT)
            dd_mult = max(0.3, 1.0 - reduction)
        return min(daily_mult, dd_mult)

    def calculate_position_size(self, entry_price, stop_price):
        try:
            account_balance = self.get_account_balance()
            base_risk = account_balance * (RISK_PER_TRADE_PCT / 100)
            multiplier = self.get_risk_multiplier()
            adjusted_risk = base_risk * multiplier
            stop_distance = abs(entry_price - stop_price)
            units = int(adjusted_risk / stop_distance)
            return max(100, min(units, 100000))
        except: return 1000

    def can_take_new_trade(self):
        self.update_equity_metrics()
        if self.current_daily_loss_pct >= BASE_DAILY_LOSS_LIMIT_PCT:
            if not self.trading_halted:
                print(f"🛑 DAILY LOSS CIRCUIT BREAKER")
                self.trading_halted = True
            return False
        if self.current_drawdown_pct >= MAX_DRAWDOWN_PCT:
            if not self.trading_halted:
                print(f"🛑 EQUITY DRAWDOWN CIRCUIT BREAKER")
                self.trading_halted = True
            return False
        return True

    def check_and_close_positions(self):
        for pair in list(self.open_positions.keys()):
            try:
                df = self.get_candles(pair, count=10)
                if df.empty: continue
                current_price = df['close'].iloc[-1]
                pos = self.open_positions[pair]
                stop = pos['stop']
                target = pos['target']
                direction = pos.get('direction', 'LONG')

                exit_reason = None
                exit_price = current_price

                if direction == 'LONG':
                    if current_price <= stop: exit_reason = "STOP_LOSS"
                    elif current_price >= target: exit_reason = "TAKE_PROFIT"
                else:
                    if current_price >= stop: exit_reason = "STOP_LOSS"
                    elif current_price <= target: exit_reason = "TAKE_PROFIT"

                if exit_reason:
                    units = pos.get('units', 1000)
                    entry = pos.get('fill_price', pos['entry'])
                    pnl = (exit_price - entry) * units * 0.0001 if direction == 'LONG' else (entry - exit_price) * units * 0.0001

                    if MODE == "live":
                        try:
                            close_data = {"longUnits": "ALL"} if direction == 'LONG' else {"shortUnits": "ALL"}
                            r = PositionClose(accountID=ACCOUNT_ID, instrument=pair, data=close_data)
                            self.safe_api_request(r)           # ← Protected
                        except Exception as e:
                            print(f"Close error: {e}")

                    self.log_trade(pos, exit_reason=exit_reason, exit_price=exit_price, pnl=round(pnl, 2))
                    del self.open_positions[pair]
                    if pair in self.trailing_stops: del self.trailing_stops[pair]
                    print(f"📉 {exit_reason} hit on {pair} ({direction}) | P&L: ${pnl:.2f}")
            except Exception as e:
                print(f"Position check error: {e}")

    def place_order(self, signal):
        if not self.can_take_new_trade(): return False
        if self.daily_trades >= MAX_DAILY_TRADES:
            print(f"⚠️ Daily trade limit reached"); return False

        direction = signal.get('direction', 'LONG')

        if MODE == "simulate":
            units = self.calculate_position_size(signal['entry'], signal['stop'])
            print(f"[SIM] {direction} {signal['instrument']} @ {signal['entry']} | Size: {units}")
            self.open_positions[signal['instrument']] = {**signal, 'direction': direction}
            self.trailing_stops[signal['instrument']] = signal['stop']
            self.daily_trades += 1
            self.log_trade(signal, units=units)
            return True

        try:
            units = self.calculate_position_size(signal['entry'], signal['stop'])
            order_units = str(units) if direction == 'LONG' else str(-units)
            data = {
                "order": {
                    "units": order_units,
                    "instrument": signal['instrument'],
                    "timeInForce": "FOK",
                    "type": "MARKET",
                    "stopLossOnFill": {"price": str(signal['stop'])},
                    "takeProfitOnFill": {"price": str(signal['target'])}
                }
            }
            r = OrderCreate(accountID=ACCOUNT_ID, data=data)
            response = self.safe_api_request(r)           # ← Protected

            if 'orderFillTransaction' in response:
                fill = response['orderFillTransaction']
                fill_price = float(fill.get('price', 0))
                filled_units = abs(int(fill.get('units', 0)))
                print(f"✅ ORDER PLACED: {direction} {signal['instrument']} | {filled_units} units @ {fill_price}")
                self.open_positions[signal['instrument']] = {
                    **signal, 'fill_price': fill_price, 'units': filled_units,
                    'order_id': fill.get('orderID'), 'direction': direction
                }
                self.trailing_stops[signal['instrument']] = signal['stop']
                self.daily_trades += 1
                self.log_trade(signal, fill_price=fill_price, units=filled_units)
                return True
            return False
        except Exception as e:
            print(f"❌ Order failed: {e}")
            return False

    def update_trailing_stop(self):
        for pair in list(self.open_positions.keys()):
            try:
                df = self.get_candles(pair, count=50)
                if df.empty: continue
                current_price = df['close'].iloc[-1]
                signal = self.open_positions[pair]
                direction = signal.get('direction', 'LONG')
                entry_price = signal.get('fill_price', signal['entry'])

                should_trail = (current_price > entry_price) if direction == 'LONG' else (current_price < entry_price)
                if should_trail:
                    atr_val = self.atr(df['high'], df['low'], df['close'])
                    if direction == 'LONG':
                        new_trail = current_price - (atr_val * TRAIL_ATR_MULTIPLIER)
                        if new_trail > self.trailing_stops.get(pair, signal['stop']):
                            self.trailing_stops[pair] = round(new_trail, 5)
                            print(f"🔄 Trail updated: {pair}")
                    else:
                        new_trail = current_price + (atr_val * TRAIL_ATR_MULTIPLIER)
                        if new_trail < self.trailing_stops.get(pair, signal['stop']):
                            self.trailing_stops[pair] = round(new_trail, 5)
                            print(f"🔄 Trail updated: {pair}")
            except: pass

    def generate_signal(self, df, instrument):
        if df.empty or len(df) < 80: return None
        try:
            close = df['close']
            rsi_val = self.rsi(close).iloc[-1]
            bb = self.bollinger_bands(close)
            adx_val = self.adx(df['high'], df['low'], close)
            atr_val = self.atr(df['high'], df['low'], close)
            price = close.iloc[-1]
            lower_bb = bb['BBL'].iloc[-1]
            upper_bb = bb['BBU'].iloc[-1]

            if rsi_val < RSI_LONG_ENTRY and price <= lower_bb * BB_MULT_LONG and adx_val < ADX_MAX:
                return {
                    "instrument": instrument, "direction": "LONG",
                    "entry": round(price, 5),
                    "stop": round(price - atr_val * STOP_ATR, 5),
                    "target": round(price + atr_val * TARGET_ATR, 5),
                    "rsi": round(rsi_val, 1), "adx": round(adx_val, 1)
                }

            if rsi_val > RSI_SHORT_ENTRY and price >= upper_bb * BB_MULT_SHORT and adx_val < ADX_MAX:
                return {
                    "instrument": instrument, "direction": "SHORT",
                    "entry": round(price, 5),
                    "stop": round(price + atr_val * STOP_ATR, 5),
                    "target": round(price - atr_val * TARGET_ATR, 5),
                    "rsi": round(rsi_val, 1), "adx": round(adx_val, 1)
                }
        except Exception as e:
            print(f"Signal error on {instrument}: {e}")
        return None

    def run_cycle(self):
        today = date.today()
        if self.daily_start_date != today:
            self.daily_trades = 0
            self.daily_start_balance = self.get_account_balance()
            self.daily_start_date = today
            self.trading_halted = False
            print("\n=== New Trading Day ===")

        if not self.is_trading_session_active():
            print(f"⏸️ Outside trading hours ({TRADE_START_HOUR}:00 - {TRADE_END_HOUR}:00 UTC)")
            time.sleep(300)
            return

        print(f"\n[{datetime.now()}] Scanning...")
        for pair in PAIRS:
            try:
                df = self.get_candles(pair)
                if df.empty: continue
                signal = self.generate_signal(df, pair)
                if signal is not None and pair not in self.open_positions:
                    print(f"🟢 SIGNAL: {signal}")
                    self.place_order(signal)
            except Exception as e:
                print(f"Error on {pair}: {e}")
            time.sleep(1)

        self.check_and_close_positions()
        self.update_trailing_stop()
        self.update_equity_metrics()

        current_balance = self.get_account_balance()
        self.equity_history.append(current_balance)

        if len(self.equity_history) % 4 == 0:
            print(f"📊 Balance: ${current_balance:,.2f} | DD: {self.current_drawdown_pct:.2f}% | Daily Loss: {self.current_daily_loss_pct:.2f}%")

if __name__ == "__main__":
    bot = VelezForexUltimate()
    while True:
        bot.run_cycle()
        time.sleep(180)
