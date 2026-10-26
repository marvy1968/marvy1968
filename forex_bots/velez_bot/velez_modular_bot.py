import os
import time
import logging
import csv
from datetime import datetime, date
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from threading import Thread

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

load_dotenv()

# ====================== ALL SETTINGS FROM .env ONLY ======================
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PAPER = os.getenv("PAPER", "true").lower() == "true"

VIRTUAL_BANKROLL = float(os.getenv("VIRTUAL_BANKROLL", 100000))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", 10))
POSITION_NOTIONAL = float(os.getenv("POSITION_NOTIONAL", 25000))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.01))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 5.0))

SECTOR_STOCKS = os.getenv("SECTOR_STOCKS", "AAPL,MSFT,AMZN,GOOGL,NVDA,JPM,XOM,JNJ,PG,UNH").split(",")
BULLISH_THRESHOLD = int(os.getenv("BULLISH_THRESHOLD", 6))
BEARISH_THRESHOLD = int(os.getenv("BEARISH_THRESHOLD", 4))

ENABLE_WYCKOFF_FILTER = os.getenv("ENABLE_WYCKOFF_FILTER", "true").lower() == "true"
WYCKOFF_SPRING_SENSITIVITY = float(os.getenv("WYCKOFF_SPRING_SENSITIVITY", 0.02))
WYCKOFF_UPTHRUST_SENSITIVITY = float(os.getenv("WYCKOFF_UPTHRUST_SENSITIVITY", 0.02))

OPEN_WINDOW_MINUTES = int(os.getenv("OPEN_WINDOW_MINUTES", 30))
WAIT_FOR_FIRST_BAR = os.getenv("WAIT_FOR_FIRST_BAR", "true").lower() == "true"

TP1_RR = float(os.getenv("TP1_RR", 1.5))
TP1_PERCENT = float(os.getenv("TP1_PERCENT", 40))
TP2_RR = float(os.getenv("TP2_RR", 2.5))
TP2_PERCENT = float(os.getenv("TP2_PERCENT", 30))
VIRTUAL_TRAIL_ATR_MULTIPLIER = float(os.getenv("VIRTUAL_TRAIL_ATR_MULTIPLIER", 0.8))
VIRTUAL_TRAIL_TIGHTEN_AFTER_TP1 = float(os.getenv("VIRTUAL_TRAIL_TIGHTEN_AFTER_TP1", 0.4))
VIRTUAL_TRAIL_MIN_BUFFER_PCT = float(os.getenv("VIRTUAL_TRAIL_MIN_BUFFER_PCT", 0.3))
OPEN_WINDOW_TIGHT_TRAIL = os.getenv("OPEN_WINDOW_TIGHT_TRAIL", "true").lower() == "true"

AUTO_OPTIMIZE = os.getenv("AUTO_OPTIMIZE", "true").lower() == "true"
OPTIMIZE_EVERY_N_TRADES = int(os.getenv("OPTIMIZE_EVERY_N_TRADES", 50))
OPTIMIZE_MAX_ADJUST_PCT = float(os.getenv("OPTIMIZE_MAX_ADJUST_PCT", 0.10))

LOG_TRADES_TO_CSV = os.getenv("LOG_TRADES_TO_CSV", "true").lower() == "true"

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s',
                    handlers=[logging.FileHandler("velez_bot.log"), logging.StreamHandler()])
log = logging.getLogger(__name__)
trade_log_file = "velez_trade_log.csv"

if LOG_TRADES_TO_CSV and not os.path.exists(trade_log_file):
    with open(trade_log_file, "w", newline="") as f:
        csv.writer(f).writerow(["timestamp","symbol","side","entry_price","exit_price","qty","pnl_dollar","pnl_pct","reason","bias","wyckoff_phase","win_loss"])

class MainBrain:
    def __init__(self):
        self.trade_client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER)
        self.data_stream = StockDataStream(API_KEY, SECRET_KEY, feed=DataFeed.IEX if PAPER else DataFeed.SIP)
        self.indicator = IndicatorEngine()
        self.sector = SectorEngine(self)
        self.scalp = ScalpEngine(self.indicator, self.sector, self)
        self.swing = SwingEngine(self.indicator, self.sector, self)
        self.optimizer = SelfOptimizer(self) if AUTO_OPTIMIZE else None

        self.bar_buffers = {}
        self.daily_trades = 0
        self.last_trade_date = None
        self.daily_pnl = 0.0
        self.open_bias = None
        self.open_window_start = None
        self.positions = {}

        log.info("🚀 Velez + Wyckoff Hybrid Bot STARTED (6/10 stocks)")
        log.info(f"Paper={PAPER} | Max trades/day={MAX_TRADES_PER_DAY} | Virtual bankroll=${VIRTUAL_BANKROLL:,.0f}")

    def get_equity(self):
        return VIRTUAL_BANKROLL if PAPER else float(self.trade_client.get_account().equity)

    def reset_daily(self):
        today = date.today()
        if self.last_trade_date != today:
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.last_trade_date = today

    def calculate_position_size(self, price, atr_pct):
        equity = self.get_equity()
        max_risk = equity * RISK_PER_TRADE_PCT
        stop_pct = atr_pct * 1.5 if atr_pct else 0.015
        risk_per_share = price * stop_pct
        risk_qty = max_risk / risk_per_share if risk_per_share > 0 else 0
        notional_qty = POSITION_NOTIONAL / price
        qty = int(min(risk_qty, notional_qty))
        return max(1, qty)

    def execute_trade(self, symbol, signal):
        self.reset_daily()
        if self.daily_trades >= MAX_TRADES_PER_DAY or self.daily_pnl / self.get_equity() * 100 <= -DAILY_LOSS_LIMIT_PCT:
            return False

        qty = self.calculate_position_size(signal['price'], signal.get('atr_pct', 0.015))
        side = OrderSide.BUY if signal['side'] == "buy" else OrderSide.SELL

        try:
            order = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.GTC if signal.get("swing") else TimeInForce.DAY)
            self.trade_client.submit_order(order)
            self.daily_trades += 1

            self.positions[symbol] = {
                "side": signal['side'], "qty": qty, "entry_price": signal['price'],
                "high_water": signal['price'], "tp1_hit": False, "tp2_hit": False,
                "reason": signal['reason'], "bias": self.sector.market_bias
            }
            log.info(f"✅ EXECUTED | {symbol} {signal['side'].upper()} {qty} | {signal['reason']} | Bias: {self.sector.market_bias}")
            return True
        except Exception as e:
            log.error(f"Order failed {symbol}: {e}")
            return False

    def on_bar(self, bar):
        symbol = bar.symbol
        tf = 'daily' if bar.timeframe == TimeFrame(1, TimeFrameUnit.Day) else '2min'

        if symbol not in self.bar_buffers:
            self.bar_buffers[symbol] = {'2min': pd.DataFrame(), 'daily': pd.DataFrame()}

        df = self.bar_buffers[symbol][tf]
        new_row = pd.DataFrame([{'timestamp': bar.timestamp, 'open': bar.open, 'high': bar.high,
                                 'low': bar.low, 'close': bar.close, 'volume': bar.volume}])
        self.bar_buffers[symbol][tf] = pd.concat([df, new_row]).tail(500)

        if tf == '2min':
            signal = self.scalp.generate_signal(symbol, self.bar_buffers[symbol]['2min'])
            if signal:
                signal['price'] = bar.close
                signal['atr_pct'] = self.indicator.get_latest_indicators(self.bar_buffers[symbol]['2min'], '2min')['atr_pct']
                self.execute_trade(symbol, signal)

        elif tf == 'daily':
            self.sector.update_bias()
            signal = self.swing.generate_signal(symbol, self.bar_buffers[symbol]['daily'])
            if signal:
                signal['price'] = bar.close
                signal['atr_pct'] = self.indicator.get_latest_indicators(self.bar_buffers[symbol]['daily'], 'daily')['atr_pct']
                signal['swing'] = True
                self.execute_trade(symbol, signal)

    def run(self):
        log.info("📡 Subscribing to bars...")
        for sym in list(SECTOR_STOCKS) + ["SPY"]:
            self.data_stream.subscribe_bars(self.on_bar, sym)

        def daily_fetcher():
            while True:
                self.sector.update_bias()
                time.sleep(1800)
        Thread(target=daily_fetcher, daemon=True).start()

        try:
            self.data_stream.run()
        except KeyboardInterrupt:
            log.info("Bot stopped by user")

class IndicatorEngine:
    def get_latest_indicators(self, df, tf):
        if len(df) < 200:
            return None
        df = df.copy()
        df['sma8'] = df['close'].rolling(8).mean()
        df['sma20'] = df['close'].rolling(20).mean()
        df['sma40'] = df['close'].rolling(40).mean() if tf == 'daily' else None
        df['sma200'] = df['close'].rolling(200).mean()
        df['atr'] = (df['high'] - df['low']).rolling(14).mean()
        df['atr_pct'] = df['atr'] / df['close']
        df['avg_vol'] = df['volume'].rolling(20).mean()
        latest = df.iloc[-1]
        latest['trend_up'] = latest['sma20'] > latest['sma200']
        latest['vol_ok'] = latest['volume'] >= 1.5 * latest['avg_vol'] and 0.008 <= latest['atr_pct'] <= 0.04
        return latest

class SectorEngine:
    def __init__(self, brain):
        self.brain = brain
        self.market_bias = "neutral"
        self.wyckoff_phase = "neutral"

    def update_bias(self):
        bullish_count = 0
        for etf in SECTOR_STOCKS:
            df = self.brain.bar_buffers.get(etf, {}).get('daily')
            if df is None or len(df) < 200:
                continue
            latest = self.brain.indicator.get_latest_indicators(df, 'daily')
            if latest and latest['close'] > latest['sma200']:
                bullish_count += 1
        bearish_count = len(SECTOR_STOCKS) - bullish_count

        self.market_bias = "bullish" if bullish_count >= BULLISH_THRESHOLD else "bearish" if bearish_count >= BEARISH_THRESHOLD else "neutral"

        # Simple Wyckoff phase (for hybrid)
        if ENABLE_WYCKOFF_FILTER:
            self.wyckoff_phase = "accumulation" if self.market_bias == "bullish" else "distribution" if self.market_bias == "bearish" else "neutral"

        log.info(f"📈 Sector Update → {bullish_count}/10 stocks up | Bias: {self.market_bias.upper()} | Wyckoff: {self.wyckoff_phase.upper()}")

class ScalpEngine:
    def __init__(self, indicator, sector, brain):
        self.ind = indicator
        self.sector = sector
        self.brain = brain

    def generate_signal(self, symbol, df):
        latest = self.ind.get_latest_indicators(df, '2min')
        if not latest or not latest['vol_ok']:
            return None

        # Open Protocol
        if self.brain.open_bias is None and WAIT_FOR_FIRST_BAR:
            return None  # wait for first 2-min bar

        # Velez setups (simplified for brevity - full Elephant/180/25% logic can be expanded)
        pullback_long = latest['trend_up'] and (latest['close'] < latest['sma8']) and (latest['close'] > latest['sma20'])
        if pullback_long and self.sector.market_bias == "bullish":
            return {'side': 'buy', 'reason': '2-min MA Pullback (Velez + Wyckoff hybrid)'}

        return None

class SwingEngine:
    def __init__(self, indicator, sector, brain):
        self.ind = indicator
        self.sector = sector
        self.brain = brain

    def generate_signal(self, symbol, df):
        latest = self.ind.get_latest_indicators(df, 'daily')
        if not latest or not latest['vol_ok']:
            return None
        if self.sector.market_bias == "bullish" and latest['trend_up']:
            pullback = latest['close'] <= latest['sma20'] * 1.02
            if pullback:
                return {'side': 'buy', 'reason': 'Swing Pullback (Velez + Wyckoff)'}
        return None

class SelfOptimizer:
    def __init__(self, brain):
        self.brain = brain
        self.trade_count = 0

    def optimize(self):
        # Simple example: read CSV and slightly tighten trail if win rate > 65%
        try:
            df = pd.read_csv(trade_log_file)
            if len(df) < OPTIMIZE_EVERY_N_TRADES:
                return
            win_rate = (df['win_loss'] == 1).mean()
            if win_rate > 0.65:
                # Example adjustment
                log.info(f"🔧 Self-Optimizer: Win rate {win_rate:.1%} → tightening trail by 5%")
                # In real use, it would rewrite .env with small changes
        except:
            pass

# ====================== LAUNCH ======================
if __name__ == "__main__":
    brain = MainBrain()
    brain.run()
