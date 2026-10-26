import os, time, logging, pytz, asyncio
from datetime import datetime
import pandas as pd
import pandas_ta_classic as ta
from dotenv import load_dotenv

from alpaca.data.live import StockDataStream, CryptoDataStream
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv(os.path.join(os.getcwd(), '.env'))

API_KEY = os.getenv("ALPACA_API_KEY").strip()
SECRET_KEY = os.getenv("ALPACA_API_SECRET").strip()

STOCK_WATCHLIST = ["NVDA", "AAPL", "MSFT", "TSLA"]
CRYPTO_WATCHLIST = ["BTC/USD", "ETH/USD"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s',
                    handlers=[logging.FileHandler("alpaca_hybrid.log", mode='a'), logging.StreamHandler()])
log = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

class MasterBeast:
    def __init__(self):
        self.trader = TradingClient(API_KEY, SECRET_KEY, paper=True)
        self.high_water_marks = {}
        self.entry_prices = {}
        self.rungs_hit = {}
        self.current_qty = {}
        self.positions_side = {} 
        self.history = {sym: pd.DataFrame(columns=['close', 'high', 'low', 'volume']) for sym in STOCK_WATCHLIST + CRYPTO_WATCHLIST}
        self.active_mode = None

    def get_market_mode(self):
        now = datetime.now(ET)
        if now.weekday() < 5 and (now.hour > 8 or (now.hour == 8 and now.minute >= 0)) and (now.hour < 15 or (now.hour == 15 and now.minute < 55)):
            return "STOCKS", STOCK_WATCHLIST
        return "CRYPTO", CRYPTO_WATCHLIST

    def execute_trade(self, symbol, side, qty, reason):
        try:
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY)
            self.trader.submit_order(order_data=req)
            log.info(f"⚡ EXECUTION: {side.name} {qty} {symbol} | {reason}")
        except Exception as e:
            log.error(f"Trade Execution Failed for {symbol}: {e}")

    def analyze_masters(self, symbol):
        df = self.history[symbol]
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

        if stage_2_uptrend and last['close'] > last['SMA_20']:
            return "HOLD_LONG", "Strong Trend Intact"
        if stage_4_downtrend and last['close'] < last['SMA_20']:
            return "HOLD_SHORT", "Strong Trend Intact"

        return "NEUTRAL", "Masters Disagree or Choppy"

    async def handle_bar(self, bar):
        symbol = bar.symbol
        price = bar.close
        expected_mode, _ = self.get_market_mode()

        # THE HANDOFF TRIGGER
        if self.active_mode and expected_mode != self.active_mode:
            log.warning(f"🔄 HANDOFF PROTOCOL: Pivoting from {self.active_mode} to {expected_mode}")
            raise Exception("Market Mode Handoff Initiated") # This breaks the stream safely

        new_row = pd.DataFrame({'close': [price], 'high': [bar.high], 'low': [bar.low], 'volume': [bar.volume]})
        self.history[symbol] = pd.concat([self.history[symbol], new_row], ignore_index=True).tail(200)

        status, reason = self.analyze_masters(symbol)

        now = datetime.now(ET)
        if expected_mode == "STOCKS" and now.hour == 15 and now.minute >= 55:
            if symbol in self.entry_prices:
                if (self.positions_side[symbol] == "LONG" and status == "HOLD_LONG") or \
                   (self.positions_side[symbol] == "SHORT" and status == "HOLD_SHORT"):
                    log.warning(f"🌙 EOD SWING: {symbol} remains strong. Holding overnight.")
                else:
                    log.warning(f"⏰ EOD FLATTEN: {symbol} momentum faded. Closing.")
                    exit_side = OrderSide.SELL if self.positions_side[symbol] == "LONG" else OrderSide.BUY
                    if self.current_qty[symbol] > 0:
                        self.execute_trade(symbol, exit_side, self.current_qty[symbol], "EOD Flatten")
                    self.clear_state(symbol)
            return

        if symbol not in self.entry_prices:
            if status == "LONG":
                self.set_entry(symbol, price, "LONG")
                self.execute_trade(symbol, OrderSide.BUY, 4, reason)
            elif status == "SHORT":
                self.set_entry(symbol, price, "SHORT")
                self.execute_trade(symbol, OrderSide.SELL, 4, reason)

        if symbol in self.entry_prices:
            entry = self.entry_prices[symbol]
            side = self.positions_side[symbol]
            pct_move = (price / entry) if side == "LONG" else (entry / price)
            rungs = {1.0075: "Rung 1", 1.0150: "Rung 2", 1.0225: "Rung 3"}

            for multiplier, label in rungs.items():
                if pct_move >= multiplier and multiplier not in self.rungs_hit[symbol]:
                    self.rungs_hit[symbol].append(multiplier)
                    if self.current_qty[symbol] > 0:
                        self.current_qty[symbol] -= 1
                        exit_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
                        log.info(f"💰 SCALE OUT: {symbol} at {price} | {label}")
                        self.execute_trade(symbol, exit_side, 1, f"Secured {label}")
                    
                    new_floor_mult = multiplier - 0.0075
                    if side == "LONG":
                        new_floor = entry * new_floor_mult
                        self.high_water_marks[symbol] = max(self.high_water_marks.get(symbol, 0), new_floor / 0.98)
                    else:
                        new_ceiling = entry / new_floor_mult
                        self.high_water_marks[symbol] = min(self.high_water_marks.get(symbol, 999999), new_ceiling / 1.02)
                    log.info(f"🛡️ TRAIL TIGHTENED for {symbol}")

            if side == "LONG":
                self.high_water_marks[symbol] = max(self.high_water_marks.get(symbol, price), price)
                if price < self.high_water_marks[symbol] * 0.98:
                    self.trigger_exit(symbol, price, OrderSide.SELL, "Long Trail Hit")
            elif side == "SHORT":
                self.high_water_marks[symbol] = min(self.high_water_marks.get(symbol, price), price)
                if price > self.high_water_marks[symbol] * 1.02:
                    self.trigger_exit(symbol, price, OrderSide.BUY, "Short Trail Hit")

    def set_entry(self, symbol, price, side):
        self.entry_prices[symbol] = price
        self.rungs_hit[symbol] = []
        self.current_qty[symbol] = 4
        self.positions_side[symbol] = side
        log.info(f"🎯 BASE ENTRY {side}: {symbol} @ {price}")

    def trigger_exit(self, symbol, price, exit_side, reason):
        log.warning(f"🛑 EXIT: {symbol} @ {price} | {reason}")
        if self.current_qty.get(symbol, 0) > 0:
            self.execute_trade(symbol, exit_side, self.current_qty[symbol], reason)
        self.clear_state(symbol)

    def clear_state(self, symbol):
        for state_dict in [self.entry_prices, self.high_water_marks, self.rungs_hit, self.current_qty, self.positions_side]:
            if symbol in state_dict: del state_dict[symbol]

    async def start(self):
        self.active_mode, watchlist = self.get_market_mode()
        log.info(f"🚀 MASTER BEAST ARMED: {self.active_mode} MODE | ALIGNING MASTERS...")
        stream = CryptoDataStream(API_KEY, SECRET_KEY) if self.active_mode == "CRYPTO" else StockDataStream(API_KEY, SECRET_KEY)
        stream.subscribe_bars(self.handle_bar, *watchlist)
        await stream._run_forever()

if __name__ == "__main__":
    bot = MasterBeast()
    while True:
        try:
            asyncio.run(bot.start())
        except Exception as e:
            if "Handoff Initiated" in str(e):
                log.info("🔀 Connecting to new market stream...")
            else:
                log.error(f"Stream Loop Pulse: {e}")
            time.sleep(5)
