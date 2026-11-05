#!/usr/bin/env python3
"""
Velez Mean Reversion Master - Forex Optimized with Real Orders
"""

import time
import pandas as pd
from datetime import datetime, timezone
import oandapyV20
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.orders import OrderCreate
import os

# ========================= CONFIG =========================
ACCOUNT_ID = os.getenv('OANDA_ACCOUNT_ID') or "YOUR_ACCOUNT_ID_HERE"
ACCESS_TOKEN = os.getenv('OANDA_TOKEN') or "REDACTED_OANDA_TOKEN"

PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD"]
GRANULARITY = "M15"
MODE = "simulate"      # Change to "live" when ready
RISK_PERCENT = 0.5

class VelezForexMRMaster:
    def __init__(self):
        print(f"🚀 Velez Forex MR Master Started - Mode: {MODE}")
        self.client = oandapyV20.API(access_token=ACCESS_TOKEN)
        self.open_positions = {}

    def get_candles(self, instrument, count=200):
        params = {"count": count, "granularity": GRANULARITY}
        r = InstrumentsCandles(instrument=instrument, params=params)
        self.client.request(r)
        data = r.response['candles']
        df = pd.DataFrame([{
            'time': c['time'],
            'open': float(c['mid']['o']),
            'high': float(c['mid']['h']),
            'low': float(c['mid']['l']),
            'close': float(c['mid']['c']),
        } for c in data])
        return df

    def rsi(self, series, period=14):
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def bollinger_bands(self, series, period=20, std=2):
        sma = series.rolling(window=period).mean()
        stddev = series.rolling(window=period).std()
        return {'BBL': sma - stddev * std, 'BBU': sma + stddev * std}

    def adx(self, high, low, close, period=14):
        try:
            tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
            plus_dm = high - high.shift()
            minus_dm = low.shift() - low
            plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
            minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
            atr = tr.rolling(period).mean()
            plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
            dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
            adx_series = dx.rolling(period).mean()
            return adx_series.iloc[-1] if not pd.isna(adx_series.iloc[-1]) else 25
        except:
            return 25

    def atr(self, high, low, close, period=14):
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    def place_order(self, signal):
        if MODE == "simulate":
            print(f"[SIM] LONG {signal['instrument']} @ {signal['entry']}")
            return True

        units = 1000  # Start small for safety

        data = {
            "order": {
                "units": str(units),
                "instrument": signal['instrument'],
                "timeInForce": "FOK",
                "type": "MARKET",
                "stopLossOnFill": {"price": str(signal['stop'])},
                "takeProfitOnFill": {"price": str(signal['target'])}
            }
        }

        try:
            r = OrderCreate(accountID=ACCOUNT_ID, data=data)
            response = self.client.request(r)
            print(f"✅ REAL ORDER PLACED on {signal['instrument']}")
            self.open_positions[signal['instrument']] = signal
            return True
        except Exception as e:
            print(f"❌ Order failed: {e}")
            return False

    def mean_reversion_signal(self, df, instrument):
        if len(df) < 80:
            return None
        try:
            close = df['close']
            rsi_val = self.rsi(close).iloc[-1]
            bb = self.bollinger_bands(close)
            adx_val = self.adx(df['high'], df['low'], close)
            atr_val = self.atr(df['high'], df['low'], close)

            price = close.iloc[-1]
            lower_bb = bb['BBL'].iloc[-1]

            if rsi_val < 33 and price <= lower_bb * 1.005 and adx_val < 23:
                stop = price - atr_val * 1.8
                target = price + atr_val * 2.5
                return {
                    "instrument": instrument,
                    "side": "long",
                    "entry": round(price, 5),
                    "stop": round(stop, 5),
                    "target": round(target, 5),
                    "rsi": round(rsi_val, 1),
                    "adx": round(adx_val, 1)
                }
        except Exception as e:
            print(f"Signal error {instrument}: {e}")
        return None

    def run_cycle(self):
        print(f"\n[{datetime.now()}] Scanning pairs...")
        for pair in PAIRS:
            try:
                df = self.get_candles(pair)
                signal = self.mean_reversion_signal(df, pair)
                if signal and pair not in self.open_positions:
                    print(f"🟢 SIGNAL DETECTED: {signal}")
                    self.place_order(signal)
            except Exception as e:
                print(f"Error on {pair}: {e}")
            time.sleep(1)

if __name__ == "__main__":
    bot = VelezForexMRMaster()
    while True:
        bot.run_cycle()
        print("Cycle complete. Sleeping 4 minutes...")
        time.sleep(240)
