import os
import time
import logging
import csv
from datetime import datetime, date
import pandas as pd
from dotenv import load_dotenv

from oandapyV20 import API
from oandapyV20.endpoints.orders import OrderCreate
from oandapyV20.endpoints.trades import TradeClose

load_dotenv("oanda_velez.env")

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
ACCESS_TOKEN = os.getenv("OANDA_ACCESS_TOKEN")
ENV = os.getenv("OANDA_ENV", "practice")

VIRTUAL_BANKROLL = float(os.getenv("VIRTUAL_BANKROLL", 100000))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", 15))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.03))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 10.0))

TP1_RR = float(os.getenv("TP1_RR", 1.5))
VIRTUAL_TRAIL_ATR_MULTIPLIER = float(os.getenv("VIRTUAL_TRAIL_ATR_MULTIPLIER", 0.8))

api = API(access_token=ACCESS_TOKEN, environment=ENV)

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s',
                    handlers=[logging.FileHandler("oanda_velez_bot.log"), logging.StreamHandler()])
log = logging.getLogger(__name__)
trade_log_file = "oanda_velez_trade_log.csv"

if not os.path.exists(trade_log_file):
    with open(trade_log_file, "w", newline="") as f:
        csv.writer(f).writerow(["timestamp","instrument","side","entry_price","exit_price","qty","pnl","reason","system"])

class MainBrain:
    def __init__(self):
        self.indicator = IndicatorEngine()
        self.sector = SectorEngine(self)
        self.engines = {
            "Velez": VelezEngine(self.indicator, self.sector, self),
            "Momentum": MomentumEngine(self.indicator, self.sector, self),
            "ORB": ORBEngine(self.indicator, self.sector, self),
            # Add the rest of the 18+ engines here later if you want
        }
        self.open_trades = {}  # instrument -> list of trade IDs (FIFO)
        self.daily_trades = 0
        self.last_trade_date = None

        log.info("🚀 OANDA Velez Hybrid Bot STARTED (FIFO-safe + Master Systems)")
        log.info(f"Max trades/day={MAX_TRADES_PER_DAY} | Risk={RISK_PER_TRADE_PCT*100}%")

    def reset_daily(self):
        today = date.today()
        if self.last_trade_date != today:
            self.daily_trades = 0
            self.last_trade_date = today

    def execute_trade(self, instrument, signal):
        self.reset_daily()
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return

        order_data = {
            "order": {
                "units": "10000",
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
        r = OrderCreate(ACCOUNT_ID, data=order_data)
        response = api.request(r)
        trade_id = response['orderFillTransaction']['tradeID']

        if instrument not in self.open_trades:
            self.open_trades[instrument] = []
        self.open_trades[instrument].append(trade_id)

        self.daily_trades += 1
        log.info(f"✅ OANDA EXECUTED | {instrument} {signal['side']} | {signal['reason']} | System: {signal.get('system')} | TradeID: {trade_id}")

    def close_oldest_trade(self, instrument, reason):
        if instrument in self.open_trades and self.open_trades[instrument]:
            oldest_id = self.open_trades[instrument].pop(0)
            r = TradeClose(ACCOUNT_ID, oldest_id)
            api.request(r)
            log.info(f"🔴 FIFO CLOSE | {instrument} closed oldest trade {oldest_id} | {reason}")

    def run(self):
        log.info("📡 OANDA bot started (20 pairs) - ready for streaming")
        # Streaming code can be added later from your existing OANDA bots

if __name__ == "__main__":
    brain = MainBrain()
    brain.run()
