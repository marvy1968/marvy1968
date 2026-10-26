import pandas as pd
import time

def report_status(df, symbol):
    rsi = df['rsi'].iloc[-1]
    adx = df['adx'].iloc[-1]
    vwap = df['vwap'].iloc[-1]
    price = df['price'].iloc[-1]
    
    print(f"📊 [{symbol}] ADX:{adx:.1f} | RSI:{rsi:.1f} | Price:{price:.5f} | VWAP:{vwap:.5f}")

# You can run this by pointing it at your log or live df
