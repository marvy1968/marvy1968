import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import v20

load_dotenv("/home/moonrider1968/bots/.env")

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_HOST = "api-fxpractice.oanda.com"

def run_backtest(instrument="EUR_USD", granularity="M15", count=2000):
    ctx = v20.Context(OANDA_HOST, 443, ssl=True, token=OANDA_API_KEY)
    resp = ctx.instrument.candles(instrument, granularity=granularity, count=count, price="M")
    
    records = []
    for c in resp.body.get('candles', []):
        if not c.complete:
            continue
        records.append({
            "time": c.time,
            "open": float(c.mid.o),
            "high": float(c.mid.h),
            "low": float(c.mid.l),
            "close": float(c.mid.c)
        })
    df = pd.DataFrame(records)
    
    # Technicals
    close = df['close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean().replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    df['bb_mid'] = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df['bb_lower'] = df['bb_mid'] - (2 * bb_std)

    low14 = df['low'].rolling(14).min()
    high14 = df['high'].rolling(14).max()
    df['stoch_k'] = 100 * ((close - low14) / (high14 - low14))
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()

    df['zscore'] = (close - df['bb_mid']) / bb_std

    tp = (df['high'] + df['low'] + close) / 3
    mad = (tp - tp.rolling(20).mean()).abs().rolling(20).mean()
    df['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * mad)

    # Consensus Voting
    b1 = df['rsi'] < 30
    b2 = df['close'] <= df['bb_lower']
    b3 = (df['stoch_k'] < 20) & (df['stoch_k'] > df['stoch_d'])
    b4 = df['zscore'] < -2.0
    b5 = df['cci'] < -100
    df['buy_votes'] = b1.astype(int) + b2.astype(int) + b3.astype(int) + b4.astype(int) + b5.astype(int)

    s1 = df['rsi'] > 55
    s2 = df['close'] >= df['bb_mid']
    s3 = df['stoch_k'] > 70
    df['sell_votes'] = s1.astype(int) + s2.astype(int) + s3.astype(int)

    # Simulation Loop
    in_pos = False
    entry_px = 0.0
    trades = []

    for i in range(25, len(df)):
        row = df.iloc[i]
        
        # Entry: 4 of 5 signals
        if not in_pos and row['buy_votes'] >= 4:
            in_pos = True
            entry_px = row['close']
            entry_time = row['time']
            continue
            
        # Exit: 2 of 3 signals OR 25 pip safety stop
        if in_pos:
            pnl_pips = (row['close'] - entry_px) * 10000
            if row['sell_votes'] >= 2 or pnl_pips <= -25.0:
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": row['time'],
                    "entry": entry_px,
                    "exit": row['close'],
                    "pips": pnl_pips
                })
                in_pos = False

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print("[!] No trades triggered with current threshold.")
        return

    wins = tdf[tdf['pips'] > 0]
    losses = tdf[tdf['pips'] <= 0]
    win_rate = (len(wins) / len(tdf)) * 100
    total_pips = tdf['pips'].sum()
    gross_win = wins['pips'].sum()
    gross_loss = abs(losses['pips'].sum())
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999.0

    print("=" * 60)
    print(f"BACKTEST RESULTS: {instrument} ({granularity}) | Sample: {len(df)} bars")
    print("=" * 60)
    print(f"Total Trades:    {len(tdf)}")
    print(f"Win Rate:        {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Net Pips:        {total_pips:+.1f} pips")
    print(f"Profit Factor:   {profit_factor}")
    print(f"Avg Trade:       {tdf['pips'].mean():+.2f} pips")
    print(f"Max Drawdown:    {tdf['pips'].min():.1f} pips")
    print("=" * 60)

if __name__ == "__main__":
    run_backtest("EUR_USD", "M15", 2000)
