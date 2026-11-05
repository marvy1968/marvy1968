import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import v20

load_dotenv("/home/moonrider1968/bots/.env")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_HOST = "api-fxpractice.oanda.com"

def test_calibrated(instrument="EUR_USD", granularity="M15", count=2000, spread_pips=1.2):
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
    if len(df) < 220:
        return

    close = df['close']
    high = df['high']
    low = df['low']

    # Volatility
    tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
    df['atr14'] = tr.rolling(14).mean()

    # ADX Filter
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / df['atr14'])
    minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / df['atr14'])
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan))
    df['adx'] = dx.rolling(14).mean()

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

    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    df['stoch_k'] = 100 * ((close - low14) / (high14 - low14))
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()
    df['zscore'] = (close - df['bb_mid']) / bb_std

    tp = (high + low + close) / 3
    mad = (tp - tp.rolling(20).mean()).abs().rolling(20).mean()
    df['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * mad)

    # 1. Long Votes
    b1 = df['rsi'] < 32
    b2 = close <= df['bb_lower']
    b3 = (df['stoch_k'] < 25) & (df['stoch_k'] > df['stoch_d'])
    b4 = df['zscore'] < -1.8
    b5 = df['cci'] < -90
    df['buy_votes'] = b1.astype(int) + b2.astype(int) + b3.astype(int) + b4.astype(int) + b5.astype(int)

    # Long Exits
    lx1 = df['rsi'] > 50
    lx2 = close >= df['bb_mid']
    lx3 = df['stoch_k'] > 65
    df['long_exit_votes'] = lx1.astype(int) + lx2.astype(int) + lx3.astype(int)

    # 2. Short Votes
    s1 = df['rsi'] > 68
    s2 = close >= df['bb_upper']
    s3 = (df['stoch_k'] > 75) & (df['stoch_k'] < df['stoch_d'])
    s4 = df['zscore'] > 1.8
    s5 = df['cci'] > 90
    df['short_votes'] = s1.astype(int) + s2.astype(int) + s3.astype(int) + s4.astype(int) + s5.astype(int)

    # Short Exits
    sx1 = df['rsi'] < 50
    sx2 = close <= df['bb_mid']
    sx3 = df['stoch_k'] < 35
    df['short_exit_votes'] = sx1.astype(int) + sx2.astype(int) + sx3.astype(int)

    pos_side = 0
    entry_px = 0.0
    stop_dist = 0.0
    trades = []
    pip_factor = 100 if "JPY" in instrument else 10000

    for i in range(50, len(df)):
        row = df.iloc[i]
        adx = row['adx']
        atr = row['atr14']

        # Entry: 3 of 5 consensus + ADX < 32 (non-runaway regime)
        if pos_side == 0 and adx < 32:
            if row['buy_votes'] >= 3:
                pos_side = 1
                entry_px = row['close']
                stop_dist = 1.75 * atr
            elif row['short_votes'] >= 3:
                pos_side = -1
                entry_px = row['close']
                stop_dist = 1.75 * atr
            continue

        if pos_side == 1:
            raw_move = row['close'] - entry_px
            if row['long_exit_votes'] >= 2 or raw_move <= -stop_dist:
                net_pips = (raw_move * pip_factor) - spread_pips
                trades.append({"side": "LONG", "pips": round(net_pips, 1)})
                pos_side = 0

        elif pos_side == -1:
            raw_move = entry_px - row['close']
            if row['short_exit_votes'] >= 2 or raw_move <= -stop_dist:
                net_pips = (raw_move * pip_factor) - spread_pips
                trades.append({"side": "SHORT", "pips": round(net_pips, 1)})
                pos_side = 0

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print(f"{instrument:<8} | Trades: 0")
        return

    wins = tdf[tdf['pips'] > 0]
    losses = tdf[tdf['pips'] <= 0]
    win_rate = (len(wins) / len(tdf)) * 100
    gross_loss = abs(losses['pips'].sum())
    pf = round(wins['pips'].sum() / gross_loss, 2) if gross_loss > 0 else 999.0

    print(f"{instrument:<8} | Trades: {len(tdf):<3} | WinRate: {win_rate:5.1f}% | Net: {tdf['pips'].sum():+6.1f} pips | PF: {pf:4.2f} | Avg: {tdf['pips'].mean():+4.2f} pips")

if __name__ == "__main__":
    print("=" * 74)
    print(f"{'PAIR':<8} | {'TRADES':<7} | {'WIN RATE':<9} | {'NET PIPS':<10} | {'PF':<5} | {'AVG TRADE'}")
    print("-" * 74)
    for p in ["EUR_USD", "GBP_USD", "EUR_GBP"]:
        test_calibrated(p, "M15", 2000, spread_pips=1.2)
    print("=" * 74)
