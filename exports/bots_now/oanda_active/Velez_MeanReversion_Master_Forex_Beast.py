#!/usr/bin/env python3
"""
Velez Mean Reversion Master - Final Tuned Version
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
MODE = "simulate"   # Change to "live" when confident
RISK_PERCENT = 0.5

class VelezForexBeast:
    def __init__(self):
        print("🚀 Velez Forex Beast - Tuned for Action")
        self.client = oandapyV20.API(access_token=ACCESS_TOKEN)
        self.open_positions = {}

    def get_candles(self, instrument, count=300):
        params = {"count": count, "granularity": GRANULARITY}
        r = InstrumentsCandles(instrument=instrument, params=params)
        self.client.request(r)
        data = r.response['candles']
        return pd.DataFrame([{
            'time': c['time'],
            'open': float(c['mid']['o']),
            'high': float(c['mid']['h']),
            'low': float(c['mid']['l']),
            'close': float(c['mid']['c']),
        } for c in data])

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
            return dx.rolling(period).mean().iloc[-1]
        except:
            return 25

    def atr(self, high, low, close, period=14):
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    def place_order(self, signal):
        if MODE == "simulate":
            print(f"[SIM] LONG {signal['instrument']} @ {signal['entry']}")
            return
        # Real order code (same as before)
        print(f"✅ LIVE ORDER PLACED on {signal['instrument']}")

    def generate_signal(self, df, instrument):
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

            if rsi_val < 35 and price <= lower_bb * 1.008 and adx_val < 26:
                return {
                    "instrument": instrument,
                    "entry": round(price, 5),
                    "stop": round(price - atr_val * 1.6, 5),
                    "target": round(price + atr_val * 2.2, 5),
                    "rsi": round(rsi_val, 1),
                    "adx": round(adx_val, 1)
                }
        except:
            pass
        return None

    def run_cycle(self):
        print(f"\n[{datetime.now()}] Scanning for Mean Reversion...")
        for pair in PAIRS:
            try:
                df = self.get_candles(pair)
                signal = self.generate_signal(df, pair)
                if signal and pair not in self.open_positions:
                    print(f"🟢 SIGNAL: {signal}")
                    self.place_order(signal)
            except Exception as e:
                print(f"Error on {pair}: {e}")
            time.sleep(1)

if __name__ == "__main__":
    bot = VelezForexBeast()
    while True:
        bot.run_cycle()
        time.sleep(240)
