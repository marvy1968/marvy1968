#!/usr/bin/env python3


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
import sqlite3
from datetime import datetime

import pandas as pd
import numpy as np
import pytz
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

NY = pytz.timezone("America/New_York")

DB="alpaca_microedge_v4.db"


class AlpacaMicroEdgeV4:

    def __init__(self):

        self.api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            base_url=os.getenv("ALPACA_BASE_URL")
        )

        # Expanded high liquidity universe
        self.symbols = [

        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","JPM","LLY",
        "V","MA","XOM","UNH","COST","HD","PG","ABBV","MRK","PEP",
        "KO","BAC","WMT","CVX","NFLX","ADBE","CRM","AMD","TMO","INTC",

        "CSCO","ORCL","QCOM","TXN","MU","AMAT","LRCX","KLAC","ADI","MCHP",
        "SNPS","CDNS","NXPI","WDAY","PANW","CRWD","ZS","NET","DDOG","SHOP",

        "UBER","LYFT","ABNB","RBLX","COIN","SQ","PYPL","ROKU","SNOW","PLTR",

        "CAT","DE","BA","HON","GE","RTX","LMT","NOC","GD","UPS",

        "TGT","LOW","SBUX","MCD","CMG","BKNG","MAR","HLT","DIS","NKE"
        ]

        self.risk_pct = float(os.getenv("RISK_PCT","0.002"))
        self.stop_pct = float(os.getenv("STOP_PCT","0.0025"))
        self.tp_pct = float(os.getenv("TP_PCT","0.006"))

        self.max_open = int(os.getenv("MAX_OPEN_TRADES","5"))
        self.cooldown = int(os.getenv("COOLDOWN_SECS","600"))

        self.last_trade = {}

        self.init_db()


    def init_db(self):

        with sqlite3.connect(DB) as c:

            c.execute("""
            CREATE TABLE IF NOT EXISTS trades
            (symbol TEXT,pnl REAL,ts TEXT)
            """)


    def market_open(self):

        clock = self.api.get_clock()

        return clock.is_open


    def account_balance(self):

        acc = self.api.get_account()

        return float(acc.equity)


    def candles(self,symbol):

        bars = self.api.get_bars(symbol,"1Min",limit=200).df

        return bars


    def rvol(self,df):

        vol = df["volume"].iloc[-1]

        avg = df["volume"].rolling(20).mean().iloc[-1]

        if avg == 0:
            return 0

        return vol/avg


    def vwap(self,df):

        p = df["close"]

        v = df["volume"]

        return (p*v).cumsum()/v.cumsum()


    def signal(self,symbol):

        df = self.candles(symbol)

        if len(df)<200:

            return None

        price = df.close.iloc[-1]

        prev_price = df.close.iloc[-2]

        ema20 = df.close.ewm(span=20).mean().iloc[-1]

        ema200 = df.close.ewm(span=200).mean().iloc[-1]

        atr = (df.high-df.low).rolling(14).mean().iloc[-1]

        atr50 = (df.high-df.low).rolling(50).mean().iloc[-1]

        if atr <= 0:

            return None

        vol_ratio = self.rvol(df)

        if vol_ratio < 1.5:

            return None

        vw = self.vwap(df).iloc[-1]

        # Market regime filter using SPY
        try:

            spy = self.candles("SPY")

            spy_price = spy.close.iloc[-1]

            spy_ema50 = spy.close.ewm(span=50).mean().iloc[-1]

            if spy_price < spy_ema50:

                return None

        except:

            pass

        pullback = price < ema20 and price > ema200

        vwap_reclaim = price > vw and prev_price < vw

        atr_expansion = atr > atr50

        bullish_confirm = (
            df.close.iloc[-1] > df.close.iloc[-2] and
            df.close.iloc[-2] > df.close.iloc[-3]
        )

        if pullback and vwap_reclaim and atr_expansion and bullish_confirm:

            return "buy"

        return None


    def position_size(self,price):

        bal = self.account_balance()

        risk = bal*self.risk_pct

        size = int(risk/(price*self.stop_pct))

        return max(size,1)


    def place_trade(self,symbol):

        price = self.api.get_latest_trade(symbol).price

        qty = self.position_size(price)

        self.api.submit_order(

        symbol=symbol,

        qty=qty,

        side="buy",

        type="market",

        time_in_force="day"

        )

        self.last_trade[symbol]=time.time()

        logging.info(f"ENTRY {symbol} {qty}")


    def run(self):

        logging.info("ALPACA MICROEDGE V4 RUNNING")

        while True:

            try:

                if not self.market_open():

                    time.sleep(60)

                    continue

                positions = self.api.list_positions()

                if len(positions)>=self.max_open:

                    time.sleep(10)

                    continue

                for symbol in self.symbols:

                    if time.time()-self.last_trade.get(symbol,0)<self.cooldown:

                        continue

                    signal = self.signal(symbol)

                    if signal=="buy":

                        self.place_trade(symbol)

                time.sleep(10)

            except Exception as e:

                logging.error(e)

                time.sleep(10)


if __name__=="__main__":

    AlpacaMicroEdgeV4().run()

