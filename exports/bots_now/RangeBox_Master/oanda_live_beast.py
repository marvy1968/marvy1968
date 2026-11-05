import pandas as pd
import numpy as np
import logging
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments

# --- INSTITUTIONAL CORE ---
class InstitutionalBrain:
    @staticmethod
    def get_signal(df):
        # Alpha Layer: Z-Score Mean Reversion
        vol = df['price'].rolling(20).std()
        dev = (df['price'] - df['vwap']) / vol
        if dev.iloc[-1] > 1.5: return "SELL_LIMIT"
        if dev.iloc[-1] < -1.5: return "BUY_LIMIT"
        return "WAIT"

    @staticmethod
    def risk_governor(daily_dd):
        # Drawdown Control: Scale exposure if DD > 1.5%
        return 0.5 if daily_dd > 0.015 else 1.0

# --- BOT ENGINE ---
class OandaLiveBeast:
    def __init__(self):
        self.client = API(access_token = "REDACTED", environment="practice")
        self.universe = ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD"]

    def run(self):
        print("🚀 [INSTITUTIONAL ENGINE ONLINE]")
        while True:
            for symbol in self.universe:
                # 1. Fetch & Calculate
                df = self.fetch_data(symbol)
                if df is None: continue
                
                # 2. Institutional Alpha Decision
                signal = InstitutionalBrain.get_signal(df)
                
                if signal != "WAIT":
                    size = InstitutionalBrain.risk_governor(0.0) # Placeholder for actual DD calc
                    print(f"✅ [INSTITUTIONAL] Routing {signal} for {symbol} | Size: {size}")
                else:
                    print(f"🔍 [WAIT] {symbol} | No Alpha detected.")

    def fetch_data(self, symbol):
        try:
            params = {"count": 100, "granularity": "M5"}
            r = instruments.InstrumentsCandles(instrument=symbol, params=params)
            self.client.request(r)
            data = [{'price': float(c['mid']['c']), 'vwap': float(c['mid']['c'])} for c in r.response['candles']]
            return pd.DataFrame(data)
        except: return None

if __name__ == "__main__":
    beast = OandaLiveBeast()
    beast.run()
