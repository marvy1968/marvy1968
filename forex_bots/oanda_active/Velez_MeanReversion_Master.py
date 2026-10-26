#!/usr/bin/env python3
"""
Velez Mean Reversion Master - OANDA FX (Repaired + Clean)
"""

import time
import pandas as pd
from datetime import datetime
import oandapyV20
from oandapyV20.endpoints.instruments import InstrumentsCandles
import os

# ========================= CONFIG =========================
ACCOUNT_ID = os.getenv('OANDA_ACCOUNT_ID') or "YOUR_REAL_ACCOUNT_ID_HERE"  # ← UPDATE THIS
ACCESS_TOKEN = os.getenv('OANDA_TOKEN') or "REDACTED_OANDA_TOKEN"
INSTRUMENT = "EUR_USD"
GRANULARITY = "M15"
COUNT = 120

class VelezMeanReversionMaster:
    def __init__(self):
        print("🚀 Velez Mean Reversion Master Started - OANDA FX")
        self.client = oandapyV20.API(access_token=ACCESS_TOKEN)
        self.instrument = INSTRUMENT
        self.position = None

    def get_candles(self):
        print(f"[{datetime.now()}] Fetching {self.instrument} {GRANULARITY} candles...")
        params = {"count": COUNT, "granularity": GRANULARITY}
        r = InstrumentsCandles(instrument=self.instrument, params=params)
        self.client.request(r)
        data = r.response['candles']
        df = pd.DataFrame([{
            'time': c['time'],
            'open': float(c['mid']['o']),
            'high': float(c['mid']['h']),
            'low': float(c['mid']['l']),
            'close': float(c['mid']['c']),
            'volume': int(c['volume'])
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
        return {'BBL': sma - stddev * std, 'BBU': sma + stddev * std, 'BBSMA': sma}

    def adx(self, high, low, close, period=14):
        try:
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            plus_dm = high - high.shift()
            minus_dm = low.shift() - low
            plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
            minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

            atr = tr.rolling(window=period).mean()
            plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

            dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
            adx_series = dx.rolling(window=period).mean()
            return adx_series.iloc[-1] if not pd.isna(adx_series.iloc[-1]) else 20
        except:
            return 20

    def mean_reversion_signal(self, df):
        if len(df) < 50:
            return None
        try:
            close = df['close']
            rsi = self.rsi(close).iloc[-1]
            bb = self.bollinger_bands(close)
            adx_val = self.adx(df['high'], df['low'], close)

            price = close.iloc[-1]
            lower_band = bb['BBL'].iloc[-1]

            oversold = rsi < 35
            near_lower = price <= lower_band * 1.008
            ranging_market = adx_val < 25

            if oversold and near_lower and ranging_market:
                strength = "strong" if rsi < 30 else "medium"
                return {"side": "long", "type": "mean_reversion", "strength": strength, "rsi": round(rsi,2), "adx": round(adx_val,1)}
        except Exception as e:
            print(f"Signal error: {e}")
        return None

    def execute_trade(self, signal):
        if signal and signal["side"] == "long":
            print(f"🟢 EXECUTING LONG: {signal}")
            self.position = "long"
        return

    def run_cycle(self):
        df = self.get_candles()
        signal = self.mean_reversion_signal(df)
        if signal:
            print(f"🟢 SIGNAL DETECTED: {signal}")
            self.execute_trade(signal)
        else:
            print("No MR signal this cycle")

if __name__ == "__main__":
    bot = VelezMeanReversionMaster()
    while True:
        bot.run_cycle()
        time.sleep(60)
