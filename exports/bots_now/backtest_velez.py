import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from alpaca_trade_api.rest import REST, TimeFrame

API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

api = REST(API_KEY, API_SECRET, BASE_URL)

def run_velez_backtest(symbol="SPY", days=120):
    print(f"Fetching {days} days of historical data for {symbol}...")
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
    
    # 1. Core Velez Indicators: 20 and 200 SMAs
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    # 2. Velez 20-SMA Pullback Bounce Strategy
    df['trend_bullish'] = df['sma_20'] > df['sma_200']
    
    # Pullback: Low touched or dipped below the 20 SMA
    df['touched_sma20'] = df['low'] <= df['sma_20']
    
    # Bounce confirmation: Close is back above the 20 SMA on a green candle
    df['bullish_candle'] = df['close'] > df['open']
    df['crossed_back_up'] = (df['close'] > df['sma_20']) & df['bullish_candle']
    
    # Signal: In a bull trend, price dipped to/below 20 SMA on previous bar and bounces now
    df['signal'] = 0
    df.loc[df['trend_bullish'] & df['touched_sma20'].shift(1) & df['crossed_back_up'], 'signal'] = 1
    
    # 3. Simulation & Metrics
    df['returns'] = df['close'].pct_change()
    df['strategy_returns'] = df['signal'].shift(1) * df['returns']
    
    total_trades = df['signal'].sum()
    cumulative_return = (1 + df['strategy_returns'].dropna()).prod() - 1
    market_return = (1 + df['returns'].dropna()).prod() - 1
    
    print("\n" + "="*45)
    print(f" VELEZ 20-SMA PULLBACK BACKTEST ({symbol})")
    print("="*45)
    print(f"Total Signals Triggered : {total_trades}")
    print(f"Strategy Return         : {cumulative_return * 100:.2f}%")
    print(f"Benchmark (Buy & Hold)  : {market_return * 100:.2f}%")
    print("="*45)
    
    return df

if __name__ == "__main__":
    run_velez_backtest("SPY", days=120)
