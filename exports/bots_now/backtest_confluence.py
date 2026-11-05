import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from alpaca_trade_api.rest import REST, TimeFrame

API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

api = REST(API_KEY, API_SECRET, BASE_URL)

def run_confluence_backtest(symbol="SPY", days=120):
    print(f"Fetching {days} days of historical data for {symbol} (Multi-System Confluence)...")
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=days)
    
    bars = api.get_bars(
        symbol, 
        TimeFrame.Hour, 
        start=start_date.strftime("%Y-%m-%d"), 
        end=end_date.strftime("%Y-%m-%d"), 
        adjustment="raw"
    ).df
    
    if bars.empty:
        print("No data returned for backtest.")
        return

    df = bars.copy()
    
    # Core Indicators
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    df['vol_sma'] = df['volume'].rolling(window=20).mean()
    
    # RSI for Elder momentum filter
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 1. Velez: Pullback Bounce (Bull trend, dipped to/below 20 SMA, closed green above 20)
    df['sys_velez'] = (df['sma_20'] > df['sma_200']) & (df['low'] <= df['sma_20']) & (df['close'] > df['sma_20']) & (df['close'] > df['open'])
    
    # 2. Minervini: Trend Template (Price above 50 and 200 SMA, 50 SMA > 200 SMA)
    df['sys_minervini'] = (df['close'] > df['sma_50']) & (df['close'] > df['sma_200']) & (df['sma_50'] > df['sma_200'])
    
    # 3. O'Neil: Volume Surge + Price Expansion (Volume > 1.5x avg & positive bar)
    df['sys_oneil'] = (df['volume'] > df['vol_sma'] * 1.5) & (df['close'] > df['open'])
    
    # 4. Darvas: 20-period High Breakout
    df['sys_darvas'] = df['close'] > df['close'].rolling(window=20).max().shift(1)
    
    # 5. Elder: Triple Screen / Momentum Filter (RSI > 50 confirming upside momentum)
    df['sys_elder'] = df['rsi'] > 50

    # Confluence Score (Count how many of the 5 systems agree)
    df['agreement_score'] = (
        df['sys_velez'].astype(int) + 
        df['sys_minervini'].astype(int) + 
        df['sys_oneil'].astype(int) + 
        df['sys_darvas'].astype(int) + 
        df['sys_elder'].astype(int)
    )

    # Trigger signal when 2 or more systems agree
    df['signal'] = 0
    df.loc[df['agreement_score'] >= 2, 'signal'] = 1

    # Simulation & Metrics
    df['returns'] = df['close'].pct_change()
    df['strategy_returns'] = df['signal'].shift(1) * df['returns']
    
    total_trades = df['signal'].sum()
    cumulative_return = (1 + df['strategy_returns'].dropna()).prod() - 1
    market_return = (1 + df['returns'].dropna()).prod() - 1
    
    print("\n" + "="*50)
    print(f" MULTI-SYSTEM CONFLUENCE BACKTEST ({symbol})")
    print("="*50)
    print(f"Total Confluence Signals : {total_trades}")
    print(f"Strategy Return          : {cumulative_return * 100:.2f}%")
    print(f"Benchmark (Buy & Hold)   : {market_return * 100:.2f}%")
    print("="*50)
    
    return df

if __name__ == "__main__":
    run_confluence_backtest("SPY", days=120)
