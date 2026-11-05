import yfinance as yf
import pandas as pd

test_syms = ["CRWD", "MSFT", "NOW", "CAT", "RTX", "SPY"]
print("[*] Fetching test data...")
raw = yf.download(test_syms, period="1y", interval="1d", progress=False, group_by="ticker")

print("=" * 80)
print(f"{'SYM':<6} | {'CLOSE':>8} | {'200 SMA':>9} | {'50 SMA':>9} | {'STAGE 2 BIAS'}")
print("-" * 80)

for sym in test_syms:
    if sym == "SPY":
        continue
    try:
        df = raw[sym].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw.dropna()
        close = float(df['Close'].iloc[-1])
        sma200 = float(df['Close'].rolling(200).mean().iloc[-1])
        sma50 = float(df['Close'].rolling(50).mean().iloc[-1])
        
        stage2 = close > sma200 and sma50 > sma200 and close >= sma50
        status = "STAGE 2 CONFIRMED" if stage2 else "BELOW TREND"
        
        print(f"{sym:<6} | ${close:>7.2f} | ${sma200:>8.2f} | ${sma50:>8.2f} | {status}")
    except Exception as e:
        print(f"{sym:<6} | Error: {e}")
print("=" * 80)
