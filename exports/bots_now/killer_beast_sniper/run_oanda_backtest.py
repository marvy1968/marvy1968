import os, sys, requests, datetime
import pandas as pd
import numpy as np

OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

if not OANDA_API_KEY:
    print("❌ FATAL: Missing OANDA_API_KEY in environment.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "AUD_USD", "USD_CHF"]
GRANULARITY = "H1"
COUNT = 1500  # ~62 days of hourly data

ACCOUNT_EQUITY = 65000.0
BASE_RISK_DOLLARS = 300.0
MAX_UNITS_HARD_CAP = 100000
MIN_UNITS = 5000
SPREAD_PIPS = {"EUR_USD": 1.2, "GBP_USD": 1.6, "USD_JPY": 1.4, "USD_CAD": 1.8, "AUD_USD": 1.5, "USD_CHF": 1.8}

def fetch_oanda_candles(pair, granularity="H1", count=1500):
    url = f"{OANDA_URL}/v3/instruments/{pair}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            print(f"Failed to fetch {pair}: {r.status_code} {r.text}")
            return None
        candles = r.json().get('candles', [])
        data = []
        for c in candles:
            if c.get('complete', False):
                mid = c.get('mid', {})
                data.append({
                    'time': c['time'],
                    'open': float(mid['o']),
                    'high': float(mid['h']),
                    'low': float(mid['l']),
                    'close': float(mid['c']),
                    'volume': float(c['volume'])
                })
        return pd.DataFrame(data)
    except Exception as e:
        print(f"Error fetching {pair}: {e}")
        return None

print(f"📥 Fetching {COUNT} {GRANULARITY} candles per pair from OANDA API...")
all_trades = []

for pair in PAIRS:
    df = fetch_oanda_candles(pair, GRANULARITY, COUNT)
    if df is None or len(df) < 100:
        continue

    pip_val = 0.01 if "JPY" in pair else 0.0001
    spread_cost_pips = SPREAD_PIPS.get(pair, 1.5)
    
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma55'] = df['close'].rolling(55).mean()
    
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    
    df['base_high'] = df['high'].shift(1).rolling(24).max()
    df['base_low'] = df['low'].shift(1).rolling(24).min()
    
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    df['plus_di'] = 100.0 * (pd.Series(plus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    df['minus_di'] = 100.0 * (pd.Series(minus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    df['adx'] = (100.0 * (abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'] + 1e-9))).rolling(14).mean()
    
    df = df.dropna().reset_index(drop=True)
    
    pos = None
    
    for i in range(55, len(df)):
        row = df.iloc[i]
        
        # Position Management & Trailing Ratchet
        if pos is not None:
            pos['bars'] += 1
            exit_trade = False
            exit_price = row['close']
            entry = pos['entry_px']
            initial_risk = pos['initial_risk']
            
            if pos['side'] == 'LONG':
                mfe = row['high'] - entry
                r_mult = mfe / initial_risk
                
                # Dynamic Ratchet: Step to BE+0.5R at +1.2R, trail EMA21 past +2.0R
                if r_mult >= 2.0:
                    pos['trail_sl'] = max(pos['trail_sl'], row['ema21'] - (0.5 * row['atr']))
                elif r_mult >= 1.2:
                    pos['trail_sl'] = max(pos['trail_sl'], entry + (0.5 * initial_risk))
                    
                exhaustion = (row['plus_di'] < row['minus_di']) and (row['close'] < row['ema21']) and (r_mult >= 0.5)
                
                if row['low'] <= pos['trail_sl']:
                    exit_price = pos['trail_sl']
                    exit_trade = True
                elif exhaustion or pos['bars'] >= 36:
                    exit_trade = True
            else:
                mfe = entry - row['low']
                r_mult = mfe / initial_risk
                
                if r_mult >= 2.0:
                    pos['trail_sl'] = min(pos['trail_sl'], row['ema21'] + (0.5 * row['atr']))
                elif r_mult >= 1.2:
                    pos['trail_sl'] = min(pos['trail_sl'], entry - (0.5 * initial_risk))
                    
                exhaustion = (row['minus_di'] < row['plus_di']) and (row['close'] > row['ema21']) and (r_mult >= 0.5)
                
                if row['high'] >= pos['trail_sl']:
                    exit_price = pos['trail_sl']
                    exit_trade = True
                elif exhaustion or pos['bars'] >= 36:
                    exit_trade = True
                    
            if exit_trade:
                pips = ((exit_price - entry) / pip_val) if pos['side'] == 'LONG' else ((entry - exit_price) / pip_val)
                net_pips = pips - spread_cost_pips
                dollar_pnl = net_pips * (pip_val * pos['units'])
                
                all_trades.append({
                    'instrument': pair, 'side': pos['side'],
                    'entry_px': entry, 'exit_px': exit_price,
                    'pips': net_pips, 'dollar_pnl': dollar_pnl,
                    'bars': pos['bars'], 'units': pos['units']
                })
                pos = None
                
        # Signal Entry (Hard-Capped Half-Kelly Sizing)
        if pos is None:
            stop_dist = max(15.0 * pip_val, 2.0 * row['atr'])
            units = int(BASE_RISK_DOLLARS / stop_dist)
            units = min(units, MAX_UNITS_HARD_CAP)
            units = max(units, MIN_UNITS)
            
            # Long Setup
            if (row['close'] > row['sma55']) and (row['close'] > row['base_high']) and (row['close'] > row['ema21']) and (row['adx'] >= 18.0):
                pos = {
                    'side': 'LONG', 'entry_px': row['close'], 'initial_risk': stop_dist,
                    'trail_sl': row['close'] - stop_dist, 'units': units, 'bars': 0
                }
            # Short Setup
            elif (row['close'] < row['sma55']) and (row['close'] < row['base_low']) and (row['close'] < row['ema21']) and (row['adx'] >= 18.0):
                pos = {
                    'side': 'SHORT', 'entry_px': row['close'], 'initial_risk': stop_dist,
                    'trail_sl': row['close'] + stop_dist, 'units': units, 'bars': 0
                }

tdf = pd.DataFrame(all_trades)
print("\n" + "=" * 95)
print("🏆 OANDA HARDENED 1-HOUR FX STRATEGY BACKTEST RESULTS (1500 BARS ~62 DAYS)")
print("=" * 95)

if not tdf.empty:
    total_trades = len(tdf)
    wins = tdf[tdf['dollar_pnl'] > 0]
    losses = tdf[tdf['dollar_pnl'] <= 0]
    wr = (len(wins) / total_trades) * 100.0
    gw = wins['dollar_pnl'].sum()
    gl = abs(losses['dollar_pnl'].sum()) if len(losses) > 0 else 1e-6
    pf = gw / gl if gl > 0 else float('inf')
    
    print(f"Total Completed Trades : {total_trades}")
    print(f"Portfolio Win Rate     : {wr:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor          : {pf:.2f}")
    print(f"Total Cumulative Net   : ${tdf['dollar_pnl'].sum():+,.2f}")
    print(f"Avg PnL / Trade        : ${tdf['dollar_pnl'].mean():+,.2f}")
    print("-" * 95)
    print(f"{'Instrument':<10} | {'Trades':<8} | {'Longs/Shorts':<14} | {'Win Rate':<10} | {'Profit Factor':<14} | {'Total Return ($)'}")
    print("-" * 95)
    for s, grp in tdf.groupby('instrument'):
        s_wr = (len(grp[grp['dollar_pnl'] > 0]) / len(grp)) * 100.0
        s_wins = grp[grp['dollar_pnl'] > 0]['dollar_pnl'].sum()
        s_loss = abs(grp[grp['dollar_pnl'] <= 0]['dollar_pnl'].sum()) if len(grp[grp['dollar_pnl'] <= 0]) > 0 else 1e-6
        s_pf = s_wins / s_loss if s_loss > 0 else float('inf')
        longs = len(grp[grp['side'] == 'LONG'])
        shorts = len(grp[grp['side'] == 'SHORT'])
        status = "🟢 PASS" if s_pf >= 1.20 else ("🟡 DEGRADE" if s_pf >= 1.0 else "🛑 ELIMINATE")
        print(f" • {s:<8} | {len(grp):<8} | {longs}L / {shorts}S{' '*6} | {s_wr:6.1f}%   | {s_pf:<14.2f} | ${grp['dollar_pnl'].sum():+9.2f} {status}")
    print("=" * 95)
else:
    print("No qualifying FX trades triggered.")
