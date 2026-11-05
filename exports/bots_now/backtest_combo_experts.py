#!/usr/bin/env python3
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv("/home/moonrider1968/bots/.env")

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# Backtest parameters
UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "AVGO", "COST"]
START_DATE = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=365)
SCORE_THRESHOLD = 75  # Trigger trade if score >= 75 / 100
ALLOCATION_PER_TRADE = 10000.0  # $10,000 position sizing

def fetch_daily_bars(symbol):
    request_params = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=START_DATE
    )
    bars = data_client.get_stock_bars(request_params)
    df = bars.df
    if df.empty:
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level='symbol')
    df = df.reset_index()
    df.rename(columns={'timestamp': 'date'}, inplace=True)
    return df

def calculate_expert_scores(df):
    close = df['close']
    high = df['high']
    low = df['low']
    vol = df['volume']

    # 1. Moving Averages & Trend
    df['sma200'] = close.rolling(200).mean()
    df['sma200_slope'] = df['sma200'].diff(10)
    df['ema20'] = close.ewm(span=20, adjust=False).mean()
    df['ema20_slope'] = df['ema20'].diff(3)

    # 2. Volatility (Minervini VCP Proxy)
    tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
    df['atr14'] = tr.rolling(14).mean()
    df['atr_contracting'] = df['atr14'] < df['atr14'].rolling(30).mean()

    # 3. Darvas 52-Week High Proximity
    df['high_52w'] = high.rolling(252, min_periods=100).max()
    df['pct_from_52w'] = (close - df['high_52w']) / df['high_52w']

    # 4. Oliver Velez Pullback & RVOL
    df['vol_ma20'] = vol.rolling(20).mean()
    df['rvol'] = vol / df['vol_ma20'].replace(0, np.nan)
    df['pullback_to_20'] = (low <= df['ema20'] * 1.015) & (close >= df['ema20'] * 0.985)

    # Scoring Pillars (25 pts each)
    s_weinstein = ((close > df['sma200']) & (df['sma200_slope'] > 0)).astype(int) * 25
    s_minervini = df['atr_contracting'].astype(int) * 25
    s_darvas = (df['pct_from_52w'] >= -0.05).astype(int) * 25
    s_velez = (df['pullback_to_20'] & (df['ema20_slope'] > 0) & (df['rvol'] >= 1.2)).astype(int) * 25

    # Weinstein Stage 2 acts as a hard filter: if Stage 2 is false, total score = 0
    raw_score = s_weinstein + s_minervini + s_darvas + s_velez
    df['score'] = np.where(s_weinstein > 0, raw_score, 0)
    return df

def backtest_symbol(symbol):
    df = fetch_daily_bars(symbol)
    if df is None or len(df) < 220:
        return []

    df = calculate_expert_scores(df)
    trades = []
    in_position = False
    entry_price, stop_loss, target_price, shares = 0.0, 0.0, 0.0, 0
    entry_date = None

    for i in range(210, len(df)):
        row = df.iloc[i]
        curr_price = float(row['close'])
        curr_high = float(row['high'])
        curr_low = float(row['low'])
        curr_date = str(row['date'])[:10]

        if not in_position:
            if row['score'] >= SCORE_THRESHOLD:
                in_position = True
                entry_price = curr_price
                entry_date = curr_date
                stop_dist = max(1.5 * float(row['atr14']), entry_price * 0.02)
                stop_loss = entry_price - stop_dist
                target_price = entry_price + (2.0 * stop_dist)  # 2R Profit Target
                shares = int(ALLOCATION_PER_TRADE / entry_price)
        else:
            # Check Stop Loss
            if curr_low <= stop_loss:
                pnl = (stop_loss - entry_price) * shares
                trades.append({
                    "symbol": symbol, "entry_date": entry_date, "exit_date": curr_date,
                    "entry_price": entry_price, "exit_price": stop_loss,
                    "shares": shares, "pnl": pnl, "pct": (stop_loss / entry_price - 1) * 100,
                    "reason": "STOP_LOSS"
                })
                in_position = False
            # Check Profit Target
            elif curr_high >= target_price:
                pnl = (target_price - entry_price) * shares
                trades.append({
                    "symbol": symbol, "entry_date": entry_date, "exit_date": curr_date,
                    "entry_price": entry_price, "exit_price": target_price,
                    "shares": shares, "pnl": pnl, "pct": (target_price / entry_price - 1) * 100,
                    "reason": "TAKE_PROFIT"
                })
                in_position = False

    return trades

def run_backtest():
    all_trades = []
    print("=" * 80)
    print("RUNNING 4-EXPERT WEIGHTED COMBO BACKTEST (Weinstein + Minervini + Darvas + Velez)")
    print("Universe: Top 10 Liquid Momentum Names | Window: 1 Year")
    print("=" * 80)

    for sym in UNIVERSE:
        t = backtest_symbol(sym)
        all_trades.extend(t)
        print(f"[{sym}] Completed: {len(t)} setups closed.")

    if not all_trades:
        print("No setups triggered meeting the >= 75 point threshold.")
        return

    tdf = pd.DataFrame(all_trades)
    total_trades = len(tdf)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    win_rate = (len(wins) / total_trades) * 100
    gross_profit = wins['pnl'].sum() if not wins.empty else 0.0
    gross_loss = abs(losses['pnl'].sum()) if not losses.empty else 0.0
    net_pnl = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else np.nan

    print("\n" + "=" * 80)
    print("PERFORMANCE REPORT: WEIGHTED 4-EXPERT MODEL")
    print("=" * 80)
    print(f"Total Trades:       {total_trades}")
    print(f"Win Rate:           {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Gross Profit:       ${gross_profit:,.2f}")
    print(f"Gross Loss:         -${gross_loss:,.2f}")
    print(f"Net Realized PnL:   ${net_pnl:,.2f}")
    print(f"Profit Factor:      {profit_factor:.2f}")
    print("=" * 80)

if __name__ == "__main__":
    run_backtest()
