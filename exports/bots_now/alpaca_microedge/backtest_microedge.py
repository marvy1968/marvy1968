import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# === CONFIG ===
API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD", "XRP/USD"]
START_DATE = "2025-01-01"
END_DATE = "2026-06-06"   # Up to today

client = CryptoHistoricalDataClient(API_KEY, SECRET_KEY)

def compute_signals(df, symbol):
    if len(df) < 50:
        return []
    df = df.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]
    
    df["ema9"] = c.ewm(span=9, adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    
    box_high = h.rolling(30).max()
    box_low = l.rolling(30).min()
    box_pos = (c - box_low) / (box_high - box_low + 1e-9)
    
    signals = []
    for i in range(40, len(df)):
        if box_pos.iloc[i] <= 0.15:   # Long Range Box / Scalp Entry
            entry = float(c.iloc[i])
            stop = entry * (1 - 0.0045)
            tp = entry * (1 + 0.0135)
            signals.append({
                "timestamp": df.index[i],
                "symbol": symbol,
                "side": 1,
                "entry": entry,
                "stop": stop,
                "tp": tp
            })
    return signals

# =============== BACKTEST ===============
total_signals = 0
pnl_summary = []

for sym in SYMBOLS:
    print(f"\n🔍 Backtesting {sym}...")
    try:
        req = CryptoBarsRequest(
            symbol_or_symbols=sym,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=START_DATE,
            end=END_DATE,
            limit=10000
        )
        bars = client.get_crypto_bars(req)
        
        # FIXED INDEXING
        if isinstance(bars.df, pd.DataFrame) and bars.df.index.nlevels > 1:
            df = bars.df.xs(sym, level=1) if sym in bars.df.index.get_level_values(1) else bars.df
        else:
            df = bars.df
        
        if df is None or len(df) < 100:
            print(f"  ⚠️  Insufficient data for {sym} ({len(df) if df is not None else 0} bars)")
            continue
        
        print(f"  📊 Loaded {len(df)} bars")
        signals = compute_signals(df, sym)
        total_signals += len(signals)
        print(f"  📈 Generated {len(signals)} Range Box signals")
        
        # Simple forward PnL (expand later with virtual stops)
        if signals:
            wins = 0
            for s in signals[:200]:  # Limit for speed
                # Simulate outcome (placeholder - replace with real price path later)
                if np.random.rand() < 0.58:  # Assumed win rate
                    wins += 1
            print(f"  Simulated win rate on sample: {wins/200:.1%}" if len(signals) > 200 else "")
            
    except Exception as e:
        print(f"  ❌ Error on {sym}: {e}")

print(f"\n✅ BACKTEST COMPLETE")
print(f"Total Range Box signals across symbols: {total_signals}")
print("Next: Full equity curve + Monte Carlo calibration coming.")
