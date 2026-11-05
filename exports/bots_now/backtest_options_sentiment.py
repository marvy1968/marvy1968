#!/usr/bin/env python3
import io
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

def get_cboe_pcr_data():
    """Fetch official total and equity put/call ratios directly from CBOE."""
    url = "https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/total_pc.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            df_cboe = pd.read_csv(io.StringIO(r.text))
            df_cboe['DATE'] = pd.to_datetime(df_cboe['DATE'], errors='coerce')
            df_cboe.dropna(subset=['DATE'], inplace=True)
            # Pick P/C Ratio column
            col = [c for c in df_cboe.columns if 'RATIO' in c.upper() or 'P/C' in c.upper()]
            if col:
                df_cboe.rename(columns={'DATE': 'Date', col[0]: 'PCR'}, inplace=True)
                df_cboe.set_index('Date', inplace=True)
                return df_cboe[['PCR']]
    except Exception as e:
        print(f"CBOE direct endpoint error: {e}")
    return None

def backtest_pcr_reversion(ticker_symbol="SPY"):
    print("=" * 80)
    print(f"RUNNING OPTIONS SENTIMENT (PCR) BACKTEST: {ticker_symbol}")
    print("=" * 80)

    # 1. Fetch CBOE data
    print("Fetching official CBOE Put/Call Ratio series...")
    cboe_pcr = get_cboe_pcr_data()

    # 2. Fetch Underlying Price History
    start_date = "2022-01-01"
    underlying = yf.download(ticker_symbol, start=start_date, progress=False)

    if underlying.empty:
        print("Failed to download underlying ETF data.")
        return

    if isinstance(underlying.columns, pd.MultiIndex):
        underlying.columns = underlying.columns.get_level_values(0)

    # If CBOE direct fetch fails, use VIX as the volatility/hedging sentiment proxy (^VIX)
    if cboe_pcr is None or cboe_pcr.empty:
        print("Note: CBOE CSV unavailable, pulling CBOE VIX (^VIX) as extreme sentiment proxy...")
        vix = yf.download("^VIX", start=start_date, progress=False)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.get_level_values(0)
        pcr_series = vix['Close']
        label = "VIX Fear Spike (>= 25)"
        is_pcr = False
    else:
        # Align index
        pcr_series = cboe_pcr['PCR'].astype(float)
        label = "Put/Call Ratio Spike (PCR >= 1.00)"
        is_pcr = True

    df = pd.DataFrame()
    df['close'] = underlying['Close']
    df['open'] = underlying['Open']
    df['high'] = underlying['High']
    df['low'] = underlying['Low']
    df['metric'] = pcr_series
    df.dropna(inplace=True)

    # Forward returns
    df['ret_1d'] = df['close'].pct_change(1).shift(-1) * 100
    df['ret_3d'] = df['close'].pct_change(3).shift(-3) * 100
    df['ret_5d'] = df['close'].pct_change(5).shift(-5) * 100

    if is_pcr:
        extreme_fear = df[df['metric'] >= 1.00]
        normal_range = df[(df['metric'] > 0.70) & (df['metric'] < 1.00)]
        complacency  = df[df['metric'] <= 0.70]
        threshold = 1.00
    else:
        extreme_fear = df[df['metric'] >= 25.0]
        normal_range = df[(df['metric'] > 16.0) & (df['metric'] < 25.0)]
        complacency  = df[df['metric'] <= 16.0]
        threshold = 25.0

    print(f"\nTotal Analyzed Days: {len(df)}")
    print(f"Extreme Fear / Hedging Days: {len(extreme_fear)}")
    print(f"Normal Sentiment Days:        {len(normal_range)}")
    print(f"Complacent / Call Days:       {len(complacency)}")

    print("\n" + "=" * 80)
    print("AVERAGE FORWARD RETURNS BY OPTIONS SENTIMENT REGIME")
    print("=" * 80)
    print(f"Extreme Fear / Hedge   -> 1D: {extreme_fear['ret_1d'].mean():+.2f}% | 3D: {extreme_fear['ret_3d'].mean():+.2f}% | 5D: {extreme_fear['ret_5d'].mean():+.2f}%")
    print(f"Normal Market Regime   -> 1D: {normal_range['ret_1d'].mean():+.2f}% | 3D: {normal_range['ret_3d'].mean():+.2f}% | 5D: {normal_range['ret_5d'].mean():+.2f}%")
    print(f"Complacency / Bullish  -> 1D: {complacency['ret_1d'].mean():+.2f}% | 3D: {complacency['ret_3d'].mean():+.2f}% | 5D: {complacency['ret_5d'].mean():+.2f}%")

    # Simulate 3-Day Swing on Fear Reversion
    trades = []
    capital = 10000.0
    for i in range(len(df) - 3):
        row = df.iloc[i]
        if row['metric'] >= threshold:
            entry_p = df.iloc[i + 1]['open']
            exit_p = df.iloc[i + 3]['close']
            pnl_pct = (exit_p / entry_p - 1)
            trades.append(capital * pnl_pct)

    if trades:
        t_s = pd.Series(trades)
        wins = t_s[t_s > 0]
        losses = t_s[t_s <= 0]
        win_rate = (len(wins) / len(t_s)) * 100
        gross_profit = wins.sum() if not wins.empty else 0.0
        gross_loss = abs(losses.sum()) if not losses.empty else 0.0
        net_pnl = t_s.sum()
        pf = gross_profit / gross_loss if gross_loss > 0 else np.nan

        print("\n" + "=" * 80)
        print(f"CONTRARIAN 3-DAY SWING PERFORMANCE ({label})")
        print("=" * 80)
        print(f"Total Trade Signals:  {len(t_s)}")
        print(f"Win Rate:             {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
        print(f"Gross Profit:         ${gross_profit:,.2f}")
        print(f"Gross Loss:           -${gross_loss:,.2f}")
        print(f"Net Realized PnL:     ${net_pnl:,.2f}")
        print(f"Profit Factor:        {pf:.2f}")
        print("=" * 80)

if __name__ == "__main__":
    backtest_pcr_reversion("SPY")
