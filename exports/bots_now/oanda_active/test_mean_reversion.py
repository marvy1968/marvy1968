import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import v20

load_dotenv("/home/moonrider1968/bots/.env")

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_HOST = "api-fxpractice.oanda.com"  # Use practice/paper environment

def fetch_oanda_candles(instrument="EUR_USD", granularity="M15", count=250):
    ctx = v20.Context(OANDA_HOST, 443, ssl=True, token=OANDA_API_KEY)
    response = ctx.instrument.candles(
        instrument,
        granularity=granularity,
        count=count,
        price="M"
    )
    
    if response.status != 200:
        raise RuntimeError(f"OANDA API Error: {response.body}")
        
    records = []
    for c in response.body.get('candles', []):
        if not c.complete:
            continue
        records.append({
            "time": c.time,
            "open": float(c.mid.o),
            "high": float(c.mid.h),
            "low": float(c.mid.l),
            "close": float(c.mid.c),
            "volume": int(c.volume)
        })
    df = pd.DataFrame(records)
    df['time'] = pd.to_datetime(df['time'])
    return df

def compute_indicators(df):
    close = df['close']
    
    # 1. RSI (14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # 2. Bollinger Bands (20, 2)
    df['bb_mid'] = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df['bb_lower'] = df['bb_mid'] - (2 * bb_std)
    df['bb_upper'] = df['bb_mid'] + (2 * bb_std)

    # 3. Stochastic Oscillator (14, 3, 3)
    low14 = df['low'].rolling(14).min()
    high14 = df['high'].rolling(14).max()
    df['stoch_k'] = 100 * ((close - low14) / (high14 - low14))
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()

    # 4. Z-Score (Distance from 20-period SMA in standard deviations)
    df['zscore'] = (close - df['bb_mid']) / bb_std

    # 5. Commodity Channel Index (CCI 20)
    tp = (df['high'] + df['low'] + close) / 3
    tp_sma = tp.rolling(20).mean()
    mad = (tp - tp_sma).abs().rolling(20).mean()
    df['cci'] = (tp - tp_sma) / (0.015 * mad)

    return df

def evaluate_consensus(df):
    df = compute_indicators(df)
    
    # 5 BUY Signals
    b1 = df['rsi'] < 30
    b2 = df['close'] <= df['bb_lower']
    b3 = (df['stoch_k'] < 20) & (df['stoch_k'] > df['stoch_d'])
    b4 = df['zscore'] < -2.0
    b5 = df['cci'] < -100
    
    df['buy_votes'] = b1.astype(int) + b2.astype(int) + b3.astype(int) + b4.astype(int) + b5.astype(int)
    
    # 3 SELL / EXIT Signals
    s1 = df['rsi'] > 55
    s2 = df['close'] >= df['bb_mid']
    s3 = df['stoch_k'] > 70
    
    df['sell_votes'] = s1.astype(int) + s2.astype(int) + s3.astype(int)
    
    return df

if __name__ == "__main__":
    instrument = "EUR_USD"
    print(f"[*] Fetching candles for {instrument}...")
    df = fetch_oanda_candles(instrument=instrument, granularity="M15", count=250)
    df = evaluate_consensus(df)
    
    latest = df.iloc[-1]
    print("=" * 60)
    print(f"Latest Bar:   {latest['time']}")
    print(f"Close Price:  {latest['close']:.5f}")
    print(f"RSI(14):      {latest['rsi']:.1f}")
    print(f"Z-Score:      {latest['zscore']:.2f}")
    print(f"CCI(20):      {latest['cci']:.1f}")
    print("-" * 60)
    print(f"BUY Consensus:  {int(latest['buy_votes'])} / 5 {'[BUY TRIGGER]' if latest['buy_votes'] >= 4 else '[NO ENTRY]'}")
    print(f"SELL Consensus: {int(latest['sell_votes'])} / 3 {'[EXIT TRIGGER]' if latest['sell_votes'] >= 2 else '[HOLD]'}")
    print("=" * 60)
