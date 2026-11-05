import os, time, logging, sqlite3, json, math
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.pricing as pricing
from oandapyV20.endpoints.accounts import AccountDetails

load_dotenv("oanda_beast.env")
OANDA_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_TOKEN = os.getenv("OANDA_ACCESS_TOKEN")

class MacroMicroBeast:
    def __init__(self):
        self.active_trades = {}
        self.daily_stages = {}
        self.api = oandapyV20.API(access_token=OANDA_TOKEN, environment="practice")
        # Spread Caps (In Pips)
        self.spread_caps = {"EUR_USD": 1.2, "GBP_USD": 1.5, "USD_JPY": 1.2, "GBP_JPY": 3.0}

    def get_spread(self, pair):
        try:
            r = pricing.PricingInfo(accountID=OANDA_ID, params={"instruments": pair})
            res = self.api.request(r)
            price = res['prices'][0]
            spread = (float(price['closeoutAsk']) - float(price['closeoutBid'])) * (100 if "JPY" in pair else 10000)
            return round(spread, 2)
        except: return 999.0

    def format_price(self, pair, price):
        return round(float(price), 3 if "JPY" in pair else 5)

    def get_fx_intelligence(self, pair):
        try:
            # SPREAD CHECK FIRST
            current_spread = self.get_spread(pair)
            max_allowed = self.spread_caps.get(pair, 2.0)
            if current_spread > max_allowed:
                return {"skip": True, "reason": f"Spread {current_spread} > {max_allowed}"}

            r = instruments.InstrumentsCandles(instrument=pair, params={"count": 50, "granularity": "M5"})
            res = self.api.request(r)
            df = pd.DataFrame([{"c": float(c['mid']['c']), "h": float(c['mid']['h']), "l": float(c['mid']['l'])} for c in res['candles']])
            c, h, l = df['c'], df['h'], df['l']
            sma20, std = c.rolling(20).mean(), c.rolling(20).std()
            tr = np.maximum(h - l, np.maximum(abs(h - c.shift(1)), abs(l - c.shift(1))))
            adx = (abs(tr.rolling(14).mean()) / tr.rolling(14).mean()).rolling(14).mean().iloc[-1] * 100
            
            return {
                'skip': False, 'price': c.iloc[-1], 'ema20': c.ewm(span=20).mean().iloc[-1],
                'upper': sma20.iloc[-1] + (std.iloc[-1] * 2),
                'lower': sma20.iloc[-1] - (std.iloc[-1] * 2),
                'adx': adx, 'is_range': adx < 25
            }
        except: return None

    def execute_fx_strike(self, pair, side, price, stop, t_type="trend"):
        try:
            bal = float(self.api.request(AccountDetails(OANDA_ID))['account']['balance'])
            risk = 0.05 if t_type == "trend" else 0.005
            stop_dist = abs(price - stop)
            if stop_dist == 0: return
            
            units = int((bal * risk) / stop_dist)
            if side == "sell": units = -units
            tp = self.format_price(pair, price + (stop_dist * 3.5) if side == "buy" else price - (stop_dist * 3.5))
            
            order = {
                "order": {
                    "units": str(units), "instrument": pair, "type": "MARKET",
                    "stopLossOnFill": {"price": str(self.format_price(pair, stop))},
                    "takeProfitOnFill": {"price": str(tp)}
                }
            }
            self.api.request(orders.OrderCreate(OANDA_ID, data=order))
            self.active_trades[pair] = {"side": side}
            logging.info(f"🔥 {t_type.upper()} STRIKE: {pair} {side.upper()} | Risk: {risk*100}%")
        except Exception as e: logging.error(f"Strike Error: {e}")

    def run(self):
        logging.info("🧠 v16.1 SAFE-STRIKE HYBRID ONLINE")
        while True:
            try:
                for pair in ["EUR_USD", "GBP_USD", "USD_JPY", "GBP_JPY"]:
                    if pair in self.active_trades: continue
                    stats = self.get_fx_intelligence(pair)
                    if not stats or stats.get('skip'): continue
                    
                    pip_val = 0.05 if "JPY" in pair else 0.0005
                    if stats['is_range']:
                        if stats['price'] >= stats['upper']:
                            self.execute_fx_strike(pair, "sell", stats['price'], stats['upper'] + pip_val, "range")
                        elif stats['price'] <= stats['lower']:
                            self.execute_fx_strike(pair, "buy", stats['price'], stats['lower'] - pip_val, "range")
                    else:
                        if stats['price'] > stats['ema20'] * 1.001:
                            self.execute_fx_strike(pair, "buy", stats['price'], stats['price'] - (pip_val * 10), "trend")
                time.sleep(45)
            except Exception as e: logging.error(f"Loop: {e}"); time.sleep(10)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
    MacroMicroBeast().run()
