import os, sys, requests, datetime
import pandas as pd
import numpy as np

OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

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
        data = [{'time': c['time'], 'open': float(c['mid']['o']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'close': float(c['mid']['c']), 'volume': float(c['volume'])} for c in r.json().get('candles', []) if c.get('complete', False)]
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
    
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma55'] = df['close'].rolling(55).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    
    tr1, tr2, tr3 = df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    
    # RSI for Momentum Pivot
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    df = df.dropna().reset_index(drop=True)
    pos = None
    
    for i in range(200, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        # 1. Manage Active Positions
        if pos is not None:
            pos['bars'] += 1
            exit_trade = False
            exit_price = row['close']
            entry = pos['entry_px']
            initial_risk = pos['initial_risk']
            
            if pos['side'] == 'LONG':
                mfe = row['high'] - entry
                r_mult = mfe / initial_risk
                
                if r_mult >= 2.0:
                    pos['trail_sl'] = max(pos['trail_sl'], row['ema21'] - (0.5 * row['atr']))
                elif r_mult >= 1.2:
                    pos['trail_sl'] = max(pos['trail_sl'], entry + (0.5 * initial_risk))
                
                if row['low'] <= pos['trail_sl']:
                    exit_price = pos['trail_sl']
                    exit_trade = True
                elif (row['close'] < row['ema21']) and (r_mult >= 0.5):
                    exit_trade = True
                    
            else:
                mfe = entry - row['low']
                r_mult = mfe / initial_risk
                
                if r_mult >= 2.0:
                    pos['trail_sl'] = min(pos['trail_sl'], row['ema21'] + (0.5 * row['atr']))
                elif r_mult >= 1.2:
                    pos['trail_sl'] = min(pos['trail_sl'], entry - (0.5 * initial_risk))
                
                if row['high'] >= pos['trail_sl']:
                    exit_price = pos['trail_sl']
                    exit_trade = True
                elif (row['close'] > row['ema21']) and (r_mult >= 0.5):
                    exit_trade = True
                    
            if exit_trade:
                pips = ((exit_price - entry) / pip_val) if pos['side'] == 'LONG' else ((entry - exit_price) / pip_val)
                net_pips = pips - spread_cost_pips
                dollar_pnl = net_pips * (pip_val * pos['units'])
                all_trades.append({'instrument': pair, 'side': pos['side'], 'pips': net_pips, 'dollar_pnl': dollar_pnl, 'bars': pos['bars']})
                pos = None
                
        # 2. Institutional Pullback Entry Logic
        if pos is None:
            # London & NY Overlap Session Filter (07:00 to 17:00 UTC)
            if 7 <= row['hour_utc'] <= 17:
                stop_dist = max(15.0 * pip_val, 1.5 * row['atr'])
                units = min(max(int(BASE_RISK_DOLLARS / stop_dist), MIN_UNITS), MAX_UNITS_HARD_CAP)
                
                # LONG Pullback: Trend is UP, price dipped to EMA21 value zone, RSI crosses back up
                bull_trend = (row['ema21'] > row['sma55']) and (row['sma55'] > row['sma200'])
                bull_pullback = (prev_row['low'] < prev_row['ema21']) and (row['close'] > row['ema21'])
                bull_momentum = (row['rsi'] > prev_row['rsi']) and (prev_row['rsi'] < 50)
                
                if bull_trend and bull_pullback and bull_momentum:
                    pos = {'side': 'LONG', 'entry_px': row['close'], 'initial_risk': stop_dist, 'trail_sl': row['close'] - stop_dist, 'units': units, 'bars': 0}
                
                # SHORT Pullback: Trend is DOWN, price bounced to EMA21 value zone, RSI crosses back down
                bear_trend = (row['ema21'] < row['sma55']) and (row['sma55'] < row['sma200'])
                bear_pullback = (prev_row['high'] > prev_row['ema21']) and (row['close'] < row['ema21'])
                bear_momentum = (row['rsi'] < prev_row['rsi']) and (prev_row['rsi'] > 50)
                
                if bear_trend and bear_pullback and bear_momentum:
                    pos = {'side': 'SHORT', 'entry_px': row['close'], 'initial_risk': stop_dist, 'trail_sl': row['close'] + stop_dist, 'units': units, 'bars': 0}

tdf = pd.DataFrame(all_trades)
print("\n" + "=" * 95)
print("🏆 OANDA VALUE-ZONE PULLBACK ENGINE BACKTEST RESULTS (1500 BARS)")
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
        print(f" • {s:<8} | Trades: {len(grp):<4} | Win Rate: {wr:5.1f}% | PF: {pf:<5.2f} | PnL: ${grp['dollar_pnl'].sum():+8.2f} {'🟢 PASS' if pf >= 1.2 else '🛑 ELIM'}")
    print("=" * 95)
else:
    print("No trades triggered.")
