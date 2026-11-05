import os, sys, requests, datetime
import pandas as pd
import numpy as np

OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

if not OANDA_API_KEY:
    print("❌ FATAL: Missing OANDA_API_KEY in environment.")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "AUD_USD", "USD_CHF"]
GRANULARITY = "H1"
COUNT = 1500

BASE_RISK_DOLLARS = 300.0
MAX_UNITS_HARD_CAP = 100000
MIN_UNITS = 5000
SPREAD_PIPS = {"EUR_USD": 1.2, "GBP_USD": 1.6, "USD_JPY": 1.4, "USD_CAD": 1.8, "AUD_USD": 1.5, "USD_CHF": 1.8}

def fetch_oanda_candles(pair, granularity="H1", count=1500):
    url = f"{OANDA_URL}/v3/instruments/{pair}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code != 200: return None
        candles = r.json().get('candles', [])
        data = [{'time': c['time'], 'open': float(c['mid']['o']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'close': float(c['mid']['c']), 'volume': float(c['volume'])} for c in candles if c.get('complete', False)]
        return pd.DataFrame(data)
    except Exception:
        return None

print(f"📥 Fetching {COUNT} {GRANULARITY} candles per pair from OANDA API...")
all_trades = []

for pair in PAIRS:
    df = fetch_oanda_candles(pair, GRANULARITY, COUNT)
    if df is None or len(df) < 100: continue

    df['time'] = pd.to_datetime(df['time'])
    df['hour_utc'] = df['time'].dt.hour
    pip_val = 0.01 if "JPY" in pair else 0.0001
    spread_cost_pips = SPREAD_PIPS.get(pair, 1.5)

    # 1. Trend & Volatility Indicators
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()

    # Bollinger Bands (20, 2.0 std)
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])

    # ATR
    tr1, tr2, tr3 = df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()

    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ADX
    up, down = df['high'] - df['high'].shift(1), df['low'].shift(1) - df['low']
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_di = 100.0 * (pd.Series(plus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    minus_di = 100.0 * (pd.Series(minus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    df['adx'] = (100.0 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))).rolling(14).mean()

    df = df.dropna().reset_index(drop=True)
    pos = None

    for i in range(200, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        # Manage Open Trade
        if pos is not None:
            pos['bars'] += 1
            exit_trade = False
            exit_price = row['close']
            entry = pos['entry_px']
            initial_risk = pos['initial_risk']

            if pos['side'] == 'LONG':
                mfe = row['high'] - entry
                r_mult = mfe / initial_risk

                if pos['strategy'] == 'MEAN_REV':
                    # Mean Reversion Target: Exit at Bollinger Midline or +1.5R
                    if row['high'] >= row['bb_mid'] or r_mult >= 1.5:
                        exit_price = max(row['bb_mid'], entry + (1.0 * initial_risk))
                        exit_trade = True
                    elif row['low'] <= pos['trail_sl'] or pos['bars'] >= 24:
                        exit_price = pos['trail_sl'] if row['low'] <= pos['trail_sl'] else row['close']
                        exit_trade = True
                else:
                    # Trend Model Target: Dynamic Ratchet
                    if r_mult >= 2.0:
                        pos['trail_sl'] = max(pos['trail_sl'], row['ema21'] - (0.5 * row['atr']))
                    elif r_mult >= 1.2:
                        pos['trail_sl'] = max(pos['trail_sl'], entry + (0.5 * initial_risk))
                    if row['low'] <= pos['trail_sl'] or pos['bars'] >= 48:
                        exit_price = pos['trail_sl'] if row['low'] <= pos['trail_sl'] else row['close']
                        exit_trade = True

            else:
                mfe = entry - row['low']
                r_mult = mfe / initial_risk

                if pos['strategy'] == 'MEAN_REV':
                    if row['low'] <= row['bb_mid'] or r_mult >= 1.5:
                        exit_price = min(row['bb_mid'], entry - (1.0 * initial_risk))
                        exit_trade = True
                    elif row['high'] >= pos['trail_sl'] or pos['bars'] >= 24:
                        exit_price = pos['trail_sl'] if row['high'] >= pos['trail_sl'] else row['close']
                        exit_trade = True
                else:
                    if r_mult >= 2.0:
                        pos['trail_sl'] = min(pos['trail_sl'], row['ema21'] + (0.5 * row['atr']))
                    elif r_mult >= 1.2:
                        pos['trail_sl'] = min(pos['trail_sl'], entry - (0.5 * initial_risk))
                    if row['high'] >= pos['trail_sl'] or pos['bars'] >= 48:
                        exit_price = pos['trail_sl'] if row['high'] >= pos['trail_sl'] else row['close']
                        exit_trade = True

            if exit_trade:
                pips = ((exit_price - entry) / pip_val) if pos['side'] == 'LONG' else ((entry - exit_price) / pip_val)
                net_pips = pips - spread_cost_pips
                dollar_pnl = net_pips * (pip_val * pos['units'])
                all_trades.append({'instrument': pair, 'strategy': pos['strategy'], 'side': pos['side'], 'pips': net_pips, 'dollar_pnl': dollar_pnl, 'bars': pos['bars']})
                pos = None

        # Entry Routing
        if pos is None:
            stop_dist = max(15.0 * pip_val, 1.5 * row['atr'])
            units = min(max(int(BASE_RISK_DOLLARS / stop_dist), MIN_UNITS), MAX_UNITS_HARD_CAP)

            # Regime Determination: Low ADX (<22) = Mean Reversion, High ADX (>=22) = Trend
            if row['adx'] < 22.0:
                # MEAN REVERSION: Buy oversold lower band, Sell overbought upper band
                if (prev['low'] <= prev['bb_lower']) and (row['close'] > row['bb_lower']) and (row['rsi'] < 38):
                    pos = {'instrument': pair, 'strategy': 'MEAN_REV', 'side': 'LONG', 'entry_px': row['close'], 'initial_risk': stop_dist, 'trail_sl': row['close'] - stop_dist, 'units': units, 'bars': 0}
                elif (prev['high'] >= prev['bb_upper']) and (row['close'] < row['bb_upper']) and (row['rsi'] > 62):
                    pos = {'instrument': pair, 'strategy': 'MEAN_REV', 'side': 'SHORT', 'entry_px': row['close'], 'initial_risk': stop_dist, 'trail_sl': row['close'] + stop_dist, 'units': units, 'bars': 0}

            elif row['adx'] >= 22.0 and (7 <= row['hour_utc'] <= 17):
                # TREND CONTINUATION: London/NY Session Only
                if (row['ema21'] > row['sma50']) and (row['sma50'] > row['sma200']) and (prev['low'] < prev['ema21']) and (row['close'] > row['ema21']):
                    pos = {'instrument': pair, 'strategy': 'TREND', 'side': 'LONG', 'entry_px': row['close'], 'initial_risk': stop_dist, 'trail_sl': row['close'] - stop_dist, 'units': units, 'bars': 0}
                elif (row['ema21'] < row['sma50']) and (row['sma50'] < row['sma200']) and (prev['high'] > prev['ema21']) and (row['close'] < row['ema21']):
                    pos = {'instrument': pair, 'strategy': 'TREND', 'side': 'SHORT', 'entry_px': row['close'], 'initial_risk': stop_dist, 'trail_sl': row['close'] + stop_dist, 'units': units, 'bars': 0}

tdf = pd.DataFrame(all_trades)
print("\n" + "=" * 95)
print("🏆 OANDA REGIME-ADAPTIVE (MEAN-REV + TREND) BACKTEST RESULTS (1500 BARS)")
print("=" * 95)

if not tdf.empty:
    gw, gl = tdf[tdf['dollar_pnl'] > 0]['dollar_pnl'].sum(), abs(tdf[tdf['dollar_pnl'] <= 0]['dollar_pnl'].sum())
    print(f"Total Completed Trades : {len(tdf)}")
    print(f"Portfolio Win Rate     : {(len(tdf[tdf['dollar_pnl'] > 0]) / len(tdf) * 100):.1f}%")
    print(f"Profit Factor          : {(gw / gl if gl > 0 else float('inf')):.2f}")
    print(f"Total Cumulative Net   : ${tdf['dollar_pnl'].sum():+,.2f}")
    print("-" * 95)
    for s, grp in tdf.groupby('instrument'):
        w, l = grp[grp['dollar_pnl'] > 0]['dollar_pnl'].sum(), abs(grp[grp['dollar_pnl'] <= 0]['dollar_pnl'].sum())
        pf = w / l if l > 0 else float('inf')
        wr = len(grp[grp['dollar_pnl'] > 0]) / len(grp) * 100
        print(f" • {s:<8} | Trades: {len(grp):<4} | Win Rate: {wr:5.1f}% | PF: {pf:<5.2f} | PnL: ${grp['dollar_pnl'].sum():+8.2f} {'🟢 PASS' if pf >= 1.2 else ('🟡 DEGRADE' if pf >= 1.0 else '🛑 ELIM')}")
    print("=" * 95)
else:
    print("No trades triggered.")
