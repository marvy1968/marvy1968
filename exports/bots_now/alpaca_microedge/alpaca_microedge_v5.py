
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import time
import logging
from datetime import datetime
import pandas as pd
import pytz
from dotenv import load_dotenv

import alpaca_trade_api as tradeapi

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

NY = pytz.timezone("America/New_York")

# ------------------------
# CONFIG
# ------------------------

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("ALPACA_BASE_URL")

BASE_RISK = float(os.getenv("BASE_RISK", 0.002))

STOP = 0.002
TP = 0.0045
BREAKEVEN = 0.003

COOLDOWN = 600

SPREAD_LIMIT = 0.0025
RVOL_LIMIT = 1.3

EMA_FAST = 20
EMA_SLOW = 200

# ------------------------
# STOCK UNIVERSE
# ------------------------

SYMBOLS = [

"AAPL","MSFT","NVDA","AMZN","META",
"GOOGL","TSLA","AVGO","JPM","XOM",

"LLY","UNH","V","MA","HD",
"COST","ABBV","MRK","PEP","KO",

"CRM","ORCL","ADBE","NFLX","AMD",
"INTC","WMT","TMO","BAC","CVX"

]

# ------------------------
# BOT
# ------------------------

class AlpacaMicroEdgeV5:

    def __init__(self):

        self.api = tradeapi.REST(
            API_KEY,
            API_SECRET,
            BASE_URL,
            api_version="v2"
        )

        self.last_trade = {}

    # ------------------------
    # MARKET OPEN
    # ------------------------

    def market_open(self):

        clock = self.api.get_clock()

        return clock.is_open

    # ------------------------
    # GET BARS
    # ------------------------

    def bars(self, symbol):

        bars = self.api.get_bars(
            symbol,
            tradeapi.TimeFrame.Minute,
            limit=250
        ).df

        return bars

    # ------------------------
    # RVOL
    # ------------------------

    def rvol(self, df):

        vol = df["volume"]

        avg = vol.rolling(50).mean().iloc[-1]

        current = vol.iloc[-1]

        if avg == 0:
            return 0

        return current / avg

    # ------------------------
    # SIGNAL
    # ------------------------

    def signal(self, symbol):

        df = self.bars(symbol)

        if len(df) < 200:
            return None

        price = df["close"].iloc[-1]

        ema20 = df["close"].ewm(span=EMA_FAST).mean().iloc[-1]
        ema200 = df["close"].ewm(span=EMA_SLOW).mean().iloc[-1]

        atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]

        dist = (price - ema20) / ema20
        trigger = (atr / price) * 2

        rvol = self.rvol(df)

        if rvol < RVOL_LIMIT:
            return None

        # LONG
        if price > ema200 and dist < -trigger:

            if df["close"].iloc[-1] > df["close"].iloc[-2]:

                return "BUY"

        # SHORT
        if price < ema200 and dist > trigger:

            if df["close"].iloc[-1] < df["close"].iloc[-2]:

                return "SELL"

        return None

    # ------------------------
    # POSITION SIZE
    # ------------------------

    def size(self, price):

        account = self.api.get_account()

        equity = float(account.equity)

        risk = equity * BASE_RISK

        qty = int(risk / price)

        if qty < 1:
            qty = 1

        return qty

    # ------------------------
    # ENTRY
    # ------------------------

    def entry(self, symbol, side):

        price = float(self.api.get_latest_trade(symbol).price)

        qty = self.size(price)

        if side == "SELL":
            qty = -qty

        try:

            self.api.submit_order(
                symbol=symbol,
                qty=abs(qty),
                side="buy" if qty > 0 else "sell",
                type="market",
                time_in_force="day"
            )

            self.last_trade[symbol] = time.time()

            logging.info(f"ENTRY {symbol} {qty}")

        except Exception as e:

            logging.error(e)

    # ------------------------
    # EXIT MANAGEMENT
    # ------------------------

    def manage(self):

        positions = self.api.list_positions()

        for p in positions:

            symbol = p.symbol

            qty = float(p.qty)

            entry = float(p.avg_entry_price)

            price = float(self.api.get_latest_trade(symbol).price)

            side = 1 if qty > 0 else -1

            pnl = ((price - entry) * side) / entry

            if pnl <= -STOP:

                self.close(symbol, qty, pnl)

            elif pnl >= TP:

                self.close(symbol, qty, pnl)

            elif pnl >= BREAKEVEN:

                if (side == 1 and price <= entry) or (side == -1 and price >= entry):

                    self.close(symbol, qty, pnl)

    # ------------------------
    # CLOSE
    # ------------------------

    def close(self, symbol, qty, pnl):

        try:

            self.api.submit_order(
                symbol=symbol,
                qty=abs(qty),
                side="sell" if qty > 0 else "buy",
                type="market",
                time_in_force="day"
            )

            logging.info(f"CLOSE {symbol} {pnl:.4%}")

        except Exception as e:

            logging.error(e)

    # ------------------------
    # RUN
    # ------------------------

    def run(self):

        logging.info("ALPACA MICROEDGE V5 RUNNING")

        while True:

            if not self.market_open():

                time.sleep(60)
                continue

            self.manage()

            for symbol in SYMBOLS:

                if time.time() - self.last_trade.get(symbol, 0) < COOLDOWN:

                    continue

                try:

                    sig = self.signal(symbol)

                    if sig:

                        self.entry(symbol, sig)

                except Exception as e:

                    logging.error(e)

            time.sleep(10)


# ------------------------
# START
# ------------------------

if __name__ == "__main__":

    AlpacaMicroEdgeV5().run()
