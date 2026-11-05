import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import v20

load_dotenv("/home/moonrider1968/bots/.env")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_HOST = "api-fxpractice.oanda.com"

def run_regime_backtest(instrument="USD_JPY", granularity="M15", count=2000, spread_pips=1.2):
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

    # 200 SMA Baseline Regime
    df['sma200'] = close.rolling(200).mean()

    # ADX Trend Strength Filter
    high = df['high']
    low = df['low']
    tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
    atr14 = tr.rolling(14).mean()
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / atr14)
    minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / atr14)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan))
    df['adx'] = dx.rolling(14).mean()

    # Core Indicators
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

    # Consensus Voting
    b1 = df['rsi'] < 30
    b2 = close <= df['bb_lower']
    b3 = (df['stoch_k'] < 20) & (df['stoch_k'] > df['stoch_d'])
    b4 = df['zscore'] < -2.0
    b5 = df['cci'] < -100
    df['buy_votes'] = b1.astype(int) + b2.astype(int) + b3.astype(int) + b4.astype(int) + b5.astype(int)

    lx1 = df['rsi'] > 55
    lx2 = close >= df['bb_mid']
    lx3 = df['stoch_k'] > 70
    df['long_exit_votes'] = lx1.astype(int) + lx2.astype(int) + lx3.astype(int)

    s1 = df['rsi'] > 70
    s2 = close >= df['bb_upper']
    s3 = (df['stoch_k'] > 80) & (df['stoch_k'] < df['stoch_d'])
    s4 = df['zscore'] > 2.0
    s5 = df['cci'] > 100
    df['short_votes'] = s1.astype(int) + s2.astype(int) + s3.astype(int) + s4.astype(int) + s5.astype(int)

    sx1 = df['rsi'] < 45
    sx2 = close <= df['bb_mid']
    sx3 = df['stoch_k'] < 30
    df['short_exit_votes'] = sx1.astype(int) + sx2.astype(int) + sx3.astype(int)

    # Backtest Loop
    pos_side = 0
    entry_px = 0.0
    trades = []
    pip_factor = 100 if "JPY" in instrument else 10000

    for i in range(200, len(df)):
        row = df.iloc[i]
        adx_val = row['adx']
        sma200_val = row['sma200']

        # Entries require: 4/5 Votes + (Price vs 200 SMA trend alignment OR Non-Trending ADX < 28)
        if pos_side == 0:
            # Long: Oversold pullback in an uptrend (Close > SMA200) or low-trend environment
            if row['buy_votes'] >= 4 and (row['close'] > sma200_val or adx_val < 26):
                pos_side = 1
                entry_px = row['close']
                entry_time = row['time']
            # Short: Overbought rip in a downtrend (Close < SMA200) or low-trend environment
            elif row['short_votes'] >= 4 and (row['close'] < sma200_val or adx_val < 26):
                pos_side = -1
                entry_px = row['close']
                entry_time = row['time']
            continue

        if pos_side == 1:
            net_pips = ((row['close'] - entry_px) * pip_factor) - spread_pips
            if row['long_exit_votes'] >= 2 or net_pips <= -25.0:
                trades.append({"side": "LONG", "pips": round(net_pips, 1)})
                pos_side = 0

        elif pos_side == -1:
            net_pips = ((entry_px - row['close']) * pip_factor) - spread_pips
            if row['short_exit_votes'] >= 2 or net_pips <= -25.0:
                trades.append({"side": "SHORT", "pips": round(net_pips, 1)})
                pos_side = 0

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print(f"[!] No trades triggered for {instrument}.")
        return

    wins = tdf[tdf['pips'] > 0]
    losses = tdf[tdf['pips'] <= 0]
    win_rate = (len(wins) / len(tdf)) * 100
    gross_loss = abs(losses['pips'].sum())
    pf = round(wins['pips'].sum() / gross_loss, 2) if gross_loss > 0 else 999.0

    print(f"REGIME-FILTERED {instrument}: Trades: {len(tdf):<2} | WinRate: {win_rate:.1f}% | Net: {tdf['pips'].sum():+6.1f} pips | PF: {pf}")

if __name__ == "__main__":
    for pair in ["EUR_USD", "USD_JPY", "GBP_USD"]:
        run_regime_backtest(pair, "M15", 2000, spread_pips=1.2)
