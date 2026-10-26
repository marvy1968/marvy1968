import os, time, logging, asyncio
from datetime import datetime
import pandas as pd
import pandas_ta_classic as ta
import requests
from dotenv import load_dotenv
import pytz

load_dotenv(os.path.join(os.getcwd(), '.env'))

OANDA_API_KEY = os.getenv("OANDA_API_KEY").strip()
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID").strip()
OANDA_URL = "https://api-fxpractice.oanda.com/v3"
HEADERS = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}

FX_WATCHLIST = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF"]
BASE_UNITS = 40000
SCALE_UNITS = 10000

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s',
                    handlers=[logging.FileHandler("oanda_master.log", mode='a'), logging.StreamHandler()])
log = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

class OandaMasterBeast:
    def __init__(self):
        self.high_water_marks = {}
        self.entry_prices = {}
        self.rungs_hit = {}
        self.current_qty = {}
        self.positions_side = {} 
        self.history = {sym: pd.DataFrame(columns=['close', 'high', 'low', 'volume']) for sym in FX_WATCHLIST}

    def execute_trade(self, symbol, side, qty, reason):
        # Oanda uses negative units for shorting
        order_qty = str(qty) if side == "LONG" else str(-qty)
        data = {
            "order": {
                "units": order_qty,
                "instrument": symbol,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
        try:
            r = requests.post(f"{OANDA_URL}/accounts/{OANDA_ACCOUNT_ID}/orders", json=data, headers=HEADERS)
            if r.status_code == 201:
                log.info(f"⚡ FX EXECUTION: {side} {qty} {symbol} | {reason}")
            else:
                log.error(f"Execution Failed {symbol}: {r.text}")
        except Exception as e:
            log.error(f"Network Error executing {symbol}: {e}")

    def fetch_m1_candles(self, symbol, count=200):
        url = f"{OANDA_URL}/instruments/{symbol}/candles?count={count}&price=M&granularity=M1"
        try:
            r = requests.get(url, headers=HEADERS)
            if r.status_code == 200:
                data = r.json().get('candles', [])
                records = []
                for c in data:
                    if c['complete']:
                        records.append({
                            'close': float(c['mid']['c']),
                            'high': float(c['mid']['h']),
                            'low': float(c['mid']['l']),
                            'volume': float(c['volume'])
                        })
                return pd.DataFrame(records)
        except Exception as e:
            log.error(f"Data fetch error for {symbol}: {e}")
        return pd.DataFrame()

    def analyze_masters(self, df):
        if len(df) < 150: return "BUFFERING", "Need 150 bars"

        df['EMA_150'] = ta.ema(df['close'], length=150)
        df['SMA_20'] = ta.sma(df['close'], length=20)
        df['SMA_Vol'] = ta.sma(df['volume'], length=20)
        
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df['MACD_Line'] = macd[macd.columns[0]]
        df['MACD_Hist'] = macd[macd.columns[1]]
        
        df['RSI'] = ta.rsi(df['close'], length=14)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        vol_spike = last['volume'] > (1.5 * last['SMA_Vol'])
        macd_bull = last['MACD_Line'] > 0 and last['MACD_Hist'] > 0
        macd_bear = last['MACD_Line'] < 0 and last['MACD_Hist'] < 0
        rsi_bull = last['RSI'] > 55
        rsi_bear = last['RSI'] < 45

        stage_2_uptrend = last['close'] > last['EMA_150'] and last['EMA_150'] > prev['EMA_150']
        stage_4_downtrend = last['close'] < last['EMA_150'] and last['EMA_150'] < prev['EMA_150']

        if stage_2_uptrend and rsi_bull and macd_bull and vol_spike:
            return "LONG", "Stage 2 Confirmed + Bull Masters Aligned"
        
        if stage_4_downtrend and rsi_bear and macd_bear and vol_spike:
            return "SHORT", "Stage 4 Confirmed + Bear Masters Aligned"

        return "NEUTRAL", "Masters Disagree or Choppy"

    async def scan_market(self):
        log.info("🚀 FX MASTER BEAST RUNNING 24/5 | SCANNING PAIRS...")
        while True:
            now = datetime.now(ET)
            
            # FRIDAY 4:50 PM FLATTEN (Weekend Gap Protection)
            if now.weekday() == 4 and now.hour == 16 and now.minute >= 50:
                log.warning("⏰ FRIDAY EOD FLATTEN: Closing all FX pairs for the weekend.")
                for sym in list(self.entry_prices.keys()):
                    if self.current_qty[sym] > 0:
                        exit_side = "SELL" if self.positions_side[sym] == "LONG" else "BUY"
                        self.execute_trade(sym, exit_side, self.current_qty[sym], "Weekend Gap Protection")
                    self.clear_state(sym)
                await asyncio.sleep(3600) # Sleep through Friday close
                continue

            for symbol in FX_WATCHLIST:
                df = self.fetch_m1_candles(symbol, 200)
                if df.empty: continue
                
                self.history[symbol] = df
                price = df.iloc[-1]['close']
                status, reason = self.analyze_masters(df)

                # 1. BASE ENTRIES
                if symbol not in self.entry_prices:
                    if status == "LONG":
                        self.set_entry(symbol, price, "LONG")
                        self.execute_trade(symbol, "LONG", BASE_UNITS, reason)
                    elif status == "SHORT":
                        self.set_entry(symbol, price, "SHORT")
                        self.execute_trade(symbol, "SHORT", BASE_UNITS, reason)

                # 2. VIRTUAL STAIRCASE + SCALE OUT
                if symbol in self.entry_prices:
                    entry = self.entry_prices[symbol]
                    side = self.positions_side[symbol]
                    pct_move = (price / entry) if side == "LONG" else (entry / price)

                    rungs = {1.002: "Rung 1", 1.004: "Rung 2", 1.006: "Rung 3"} # Scaled for FX Volatility

                    for multiplier, label in rungs.items():
                        if pct_move >= multiplier and multiplier not in self.rungs_hit[symbol]:
                            self.rungs_hit[symbol].append(multiplier)
                            
                            if self.current_qty[symbol] > 0:
                                self.current_qty[symbol] -= SCALE_UNITS
                                exit_side = "SELL" if side == "LONG" else "BUY"
                                log.info(f"💰 SCALE OUT: {symbol} at {price} | {label}")
                                self.execute_trade(symbol, exit_side, SCALE_UNITS, f"Secured {label}")
                            
                            # Tighten Trail
                            new_floor_mult = multiplier - 0.001
                            if side == "LONG":
                                new_floor = entry * new_floor_mult
                                self.high_water_marks[symbol] = max(self.high_water_marks.get(symbol, 0), new_floor / 0.998)
                            else:
                                new_ceiling = entry / new_floor_mult
                                self.high_water_marks[symbol] = min(self.high_water_marks.get(symbol, 999999), new_ceiling / 1.002)

                    # 3. DYNAMIC TRAIL EXITS (-0.2% from Peak for FX)
                    if side == "LONG":
                        self.high_water_marks[symbol] = max(self.high_water_marks.get(symbol, price), price)
                        if price < self.high_water_marks[symbol] * 0.998:
                            self.trigger_exit(symbol, price, "SELL", "Long Trail Hit")
                    elif side == "SHORT":
                        self.high_water_marks[symbol] = min(self.high_water_marks.get(symbol, price), price)
                        if price > self.high_water_marks[symbol] * 1.002:
                            self.trigger_exit(symbol, price, "BUY", "Short Trail Hit")

            await asyncio.sleep(60) # Poll every 60 seconds (1-minute bars)

    def set_entry(self, symbol, price, side):
        self.entry_prices[symbol] = price
        self.rungs_hit[symbol] = []
        self.current_qty[symbol] = BASE_UNITS
        self.positions_side[symbol] = side
        log.info(f"🎯 FX BASE ENTRY {side}: {symbol} @ {price}")

    def trigger_exit(self, symbol, price, exit_side, reason):
        log.warning(f"🛑 FX EXIT: {symbol} @ {price} | {reason}")
        if self.current_qty.get(symbol, 0) > 0:
            self.execute_trade(symbol, exit_side, self.current_qty[symbol], reason)
        self.clear_state(symbol)

    def clear_state(self, symbol):
        for state_dict in [self.entry_prices, self.high_water_marks, self.rungs_hit, self.current_qty, self.positions_side]:
            if symbol in state_dict: del state_dict[symbol]

if __name__ == "__main__":
    bot = OandaMasterBeast()
    while True:
        try:
            asyncio.run(bot.scan_market())
        except Exception as e:
            log.error(f"Loop Pulse: {e}")
            time.sleep(15)
