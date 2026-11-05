import os
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from alpaca_trade_api.rest import REST, TimeFrame

API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

api = REST(API_KEY, API_SECRET, BASE_URL)

def run_sniper_backtest(symbol="SPY", days=120):
    print(f"Fetching {days} days of historical data for {symbol} and market benchmark (SPY)...")
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=days + 50)  # Extra buffer for 200 SMA
    
    # Fetch target symbol bars
    bars = api.get_bars(
        symbol, 
        TimeFrame.Hour, 
        start=start_date.strftime("%Y-%m-%d"), 
        end=end_date.strftime("%Y-%m-%d"), 
        adjustment="raw"
    ).df
    
    if bars.empty:
        print(f"No data returned for {symbol}.")
        return

    # Fetch SPY benchmark bars for market regime gate if symbol is not SPY
    if symbol.upper() != "SPY":
        spy_bars = api.get_bars(
            "SPY", 
            TimeFrame.Hour, 
            start=start_date.strftime("%Y-%m-%d"), 
            end=end_date.strftime("%Y-%m-%d"), 
            adjustment="raw"
        ).df
    else:
        spy_bars = bars.copy()

    df = bars.copy()
    df['spy_close'] = spy_bars['close'] if not spy_bars.empty else df['close']

    # --- INDICATORS ---
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    df['spy_sma_200'] = df['spy_close'].rolling(window=200).mean()
    df['vol_sma'] = df['volume'].rolling(window=20).mean()

    # ATR(14) Calculation
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = true_range.rolling(window=14).mean()

    # RSI for Elder momentum filter
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # --- 2. TREND REGIME GATE ---
    df['regime_pass'] = (df['close'] > df['sma_200']) & (df['spy_close'] > df['spy_sma_200'])

    # --- 1. FIVE SYSTEMS SPEC ---
    df['sys_velez'] = (df['sma_20'] > df['sma_200']) & (df['low'] <= df['sma_20']) & (df['close'] > df['sma_20']) & (df['close'] > df['open'])
    df['sys_minervini'] = (df['close'] > df['sma_50']) & (df['close'] > df['sma_200']) & (df['sma_50'] > df['sma_200'])
    df['sys_oneil'] = (df['volume'] > df['vol_sma'] * 1.5) & (df['close'] > df['open'])
    df['sys_darvas'] = df['close'] > df['close'].rolling(window=20).max().shift(1)
    df['sys_elder'] = df['rsi'] > 50

    df['agreement_score'] = (
        df['sys_velez'].astype(int) + 
        df['sys_minervini'].astype(int) + 
        df['sys_oneil'].astype(int) + 
        df['sys_darvas'].astype(int) + 
        df['sys_elder'].astype(int)
    )

    # --- 1. SIGNAL THRESHOLD (MIN_SIGNAL_SCORE = 3) + REGIME GATE ---
    df['signal'] = 0
    df.loc[df['regime_pass'] & (df['agreement_score'] >= 3), 'signal'] = 1

    # Trim back to requested day range (cast to int to fix indexer type error)
    df = df.tail(int(days * 6.5))

    # --- 3. ATR BRACKET SIMULATION & METRICS ---
    df['returns'] = df['close'].pct_change()
    df['strategy_returns'] = df['signal'].shift(1) * df['returns']
    
    total_signals = df['signal'].sum()
    regime_blocked_count = (~df['regime_pass']).sum()
    cumulative_return = (1 + df['strategy_returns'].dropna()).prod() - 1
    market_return = (1 + df['returns'].dropna()).prod() - 1
    
    print("\n" + "="*55)
    print(f" SNIPER CONFLUENCE BACKTEST REPORT ({symbol})")
    print("="*55)
    print(f"Total Bars Evaluated     : {len(df)}")
    print(f"Regime Gate Blocked Bars : {regime_blocked_count}")
    print(f"Valid Sniper Signals     : {total_signals}")
    print(f"Strategy Return          : {cumulative_return * 100:.2f}%")
    print(f"Benchmark Return         : {market_return * 100:.2f}%")
    print("="*55)
    
    if total_signals > 0:
        print("System Trigger Contributions (when signals fired):")
        print(f"  - Velez Pullback     : {df[df['signal'] == 1]['sys_velez'].sum()}")
        print(f"  - Minervini Template : {df[df['signal'] == 1]['sys_minervini'].sum()}")
        print(f"  - O'Neil Volume      : {df[df['signal'] == 1]['sys_oneil'].sum()}")
        print(f"  - Darvas Breakout    : {df[df['signal'] == 1]['sys_darvas'].sum()}")
        print(f"  - Elder Momentum     : {df[df['signal'] == 1]['sys_elder'].sum()}")
        print("="*55)

    return df

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Sniper Backtest")
    parser.add_argument("--symbol", type=str, default="SPY", help="Ticker symbol to test")
    parser.add_argument("--days", type=int, default=120, help="Number of days to backtest")
    args = parser.parse_args()
    
    run_sniper_backtest(args.symbol, args.days)
