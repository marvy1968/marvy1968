import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import v20

load_dotenv("/home/moonrider1968/bots/.env")

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_HOST = "api-fxpractice.oanda.com"

def run_twoway_backtest(instrument="EUR_USD", granularity="M15", count=2000, spread_pips=1.2):
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
    close = df['close']

    # Technical Indicators
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean().replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    df['bb_mid'] = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df['bb_lower'] = df['bb_mid'] - (2 * bb_std)
    df['bb_upper'] = df['bb_mid'] + (2 * bb_std)

    low14 = df['low'].rolling(14).min()
    high14 = df['high'].rolling(14).max()
    df['stoch_k'] = 100 * ((close - low14) / (high14 - low14))
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()

    df['zscore'] = (close - df['bb_mid']) / bb_std

    tp = (df['high'] + df['low'] + close) / 3
    mad = (tp - tp.rolling(20).mean()).abs().rolling(20).mean()
    df['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * mad)

    # 1. Long Voting Consensus (Oversold)
    b1 = df['rsi'] < 30
    b2 = df['close'] <= df['bb_lower']
    b3 = (df['stoch_k'] < 20) & (df['stoch_k'] > df['stoch_d'])
    b4 = df['zscore'] < -2.0
    b5 = df['cci'] < -100
    df['buy_votes'] = b1.astype(int) + b2.astype(int) + b3.astype(int) + b4.astype(int) + b5.astype(int)

    # Long Exits
    lx1 = df['rsi'] > 55
    lx2 = df['close'] >= df['bb_mid']
    lx3 = df['stoch_k'] > 70
    df['long_exit_votes'] = lx1.astype(int) + lx2.astype(int) + lx3.astype(int)

    # 2. Short Voting Consensus (Overbought)
    s1 = df['rsi'] > 70
    s2 = df['close'] >= df['bb_upper']
    s3 = (df['stoch_k'] > 80) & (df['stoch_k'] < df['stoch_d'])
    s4 = df['zscore'] > 2.0
    s5 = df['cci'] > 100
    df['short_votes'] = s1.astype(int) + s2.astype(int) + s3.astype(int) + s4.astype(int) + s5.astype(int)

    # Short Exits
    sx1 = df['rsi'] < 45
    sx2 = df['close'] <= df['bb_mid']
    sx3 = df['stoch_k'] < 30
    df['short_exit_votes'] = sx1.astype(int) + sx2.astype(int) + sx3.astype(int)

    # Simulation
    pos_side = 0  # 1 = Long, -1 = Short, 0 = Flat
    entry_px = 0.0
    trades = []
    pip_factor = 100 if "JPY" in instrument else 10000

    for i in range(25, len(df)):
        row = df.iloc[i]

        # No Position: check entries
        if pos_side == 0:
            if row['buy_votes'] >= 4:
                pos_side = 1
                entry_px = row['close']
                entry_time = row['time']
            elif row['short_votes'] >= 4:
                pos_side = -1
                entry_px = row['close']
                entry_time = row['time']
            continue

        # In Long Position
        if pos_side == 1:
            raw_pips = (row['close'] - entry_px) * pip_factor
            net_pips = raw_pips - spread_pips
            if row['long_exit_votes'] >= 2 or net_pips <= -25.0:
                trades.append({
                    "side": "LONG", "entry_time": entry_time, "exit_time": row['time'],
                    "entry": entry_px, "exit": row['close'], "pips": round(net_pips, 1)
                })
                pos_side = 0

        # In Short Position
        elif pos_side == -1:
            raw_pips = (entry_px - row['close']) * pip_factor
            net_pips = raw_pips - spread_pips
            if row['short_exit_votes'] >= 2 or net_pips <= -25.0:
                trades.append({
                    "side": "SHORT", "entry_time": entry_time, "exit_time": row['time'],
                    "entry": entry_px, "exit": row['close'], "pips": round(net_pips, 1)
                })
                pos_side = 0

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print(f"[!] No trades triggered for {instrument}.")
        return

    wins = tdf[tdf['pips'] > 0]
    losses = tdf[tdf['pips'] <= 0]
    win_rate = (len(wins) / len(tdf)) * 100
    total_pips = tdf['pips'].sum()
    gross_win = wins['pips'].sum()
    gross_loss = abs(losses['pips'].sum())
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999.0

    print("=" * 65)
    print(f"TWO-WAY CONSENSUS BACKTEST: {instrument} ({granularity})")
    print(f"Spread Deducted: {spread_pips} pips/trade | Total Bars: {len(df)}")
    print("=" * 65)
    print(f"Total Trades:     {len(tdf)} (Longs: {len(tdf[tdf['side']=='LONG'])}, Shorts: {len(tdf[tdf['side']=='SHORT'])})")
    print(f"Win Rate:         {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Net Realized:     {total_pips:+.1f} pips")
    print(f"Profit Factor:    {profit_factor}")
    print(f"Avg Trade Pips:   {tdf['pips'].mean():+.2f} pips")
    print(f"Worst Trade:      {tdf['pips'].min():.1f} pips")
    print(f"Best Trade:       {tdf['pips'].max():.1f} pips")
    print("=" * 65)

if __name__ == "__main__":
    for pair in ["EUR_USD", "USD_JPY", "GBP_USD"]:
        run_twoway_backtest(pair, "M15", 2000, spread_pips=1.2)
        print()
