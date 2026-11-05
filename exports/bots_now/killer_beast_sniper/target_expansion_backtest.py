import os, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv('/home/moonrider1968/bots/killer_beast_sniper/.env')
API_KEY = os.getenv('APCA_API_KEY_ID')
SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': SECRET_KEY}

def fetch_bars(symbol, timeframe, limit=1000):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    params = {
        'timeframe': timeframe,
        'start': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'end': end.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'limit': limit,
        'adjustment': 'split',
        'feed': 'iex'
    }
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        return pd.DataFrame()
    bars = r.json().get('bars', [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df['t'] = pd.to_datetime(df['t'])
    df.set_index('t', inplace=True)
    return df

def run_comparison(tickers=['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'KO', 'LLY', 'XOM', 'NFLX']):
    print("=" * 75)
    print("🔬 TARGET EXPANSION BACKTEST: BASELINE (+0.85R) vs. 3.0% vs. 5.0%")
    print("=" * 75)
    
    stats = {
        '0.85R_Scalp': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0},
        '3.0%_Target': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0},
        '5.0%_Target': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0}
    }
    
    for sym in tickers:
        df_1h = fetch_bars(sym, '1Hour', limit=500)
        df_1m = fetch_bars(sym, '1Min', limit=5000)
        
        if df_1h.empty or df_1m.empty:
            continue
            
        # HTF Trend (1H)
        df_1h['ema20'] = df_1h['c'].ewm(span=20, adjust=False).mean()
        df_1h['ema50'] = df_1h['c'].ewm(span=50, adjust=False).mean()
        df_1h['trend_bias'] = np.where(df_1h['ema20'] > df_1h['ema50'], 1, -1)
        
        df_1m['htf_bias'] = df_1h['trend_bias'].reindex(df_1m.index, method='ffill')
        
        # Micro Indicators (1M)
        delta = df_1m['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=2).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=2).mean()
        rs = gain / (loss + 1e-9)
        df_1m['rsi2'] = 100 - (100 / (1 + rs))
        df_1m['ema20_1m'] = df_1m['c'].ewm(span=20, adjust=False).mean()
        
        # ATR Calculation
        tr1 = df_1m['h'] - df_1m['l']
        tr2 = (df_1m['h'] - df_1m['c'].shift()).abs()
        tr3 = (df_1m['l'] - df_1m['c'].shift()).abs()
        df_1m['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
        
        for i in range(20, len(df_1m) - 60):
            row = df_1m.iloc[i]
            prev = df_1m.iloc[i-1]
            
            is_long = prev['rsi2'] < 10 and row['c'] > row['ema20_1m'] and row['htf_bias'] == 1
            is_short = prev['rsi2'] > 90 and row['c'] < row['ema20_1m'] and row['htf_bias'] == -1
            
            if not (is_long or is_short):
                continue
                
            entry = row['c']
            atr_val = row['atr'] if row['atr'] > 0 else entry * 0.005
            stop_dist = max(atr_val * 0.75, entry * 0.0025)
            
            # Forward window (up to 60 1-minute bars)
            future = df_1m.iloc[i+1:i+61]
            
            # --- 1. Evaluate 0.85R Scalp Target ---
            stats['0.85R_Scalp']['trades'] += 1
            tp_085 = entry + (stop_dist * 0.85) if is_long else entry - (stop_dist * 0.85)
            sl_085 = entry - stop_dist if is_long else entry + stop_dist
            
            win_085 = False
            for _, fbar in future.iterrows():
                if is_long:
                    if fbar['h'] >= tp_085: win_085 = True; break
                    if fbar['l'] <= sl_085: break
                else:
                    if fbar['l'] <= tp_085: win_085 = True; break
                    if fbar['h'] >= sl_085: break
            if win_085:
                stats['0.85R_Scalp']['wins'] += 1
                stats['0.85R_Scalp']['pnl_r'] += 0.85
            else:
                stats['0.85R_Scalp']['losses'] += 1
                stats['0.85R_Scalp']['pnl_r'] -= 1.0

            # --- 2. Evaluate 3.0% Target ---
            stats['3.0%_Target']['trades'] += 1
            tp_3pct = entry * 1.03 if is_long else entry * 0.97
            sl_3pct = entry - (stop_dist * 1.5) if is_long else entry + (stop_dist * 1.5)
            
            win_3 = False
            for _, fbar in future.iterrows():
                if is_long:
                    if fbar['h'] >= tp_3pct: win_3 = True; break
                    if fbar['l'] <= sl_3pct: break
                else:
                    if fbar['l'] <= tp_3pct: win_3 = True; break
                    if fbar['h'] >= sl_3pct: break
            if win_3:
                stats['3.0%_Target']['wins'] += 1
                stats['3.0%_Target']['pnl_r'] += 3.0
            else:
                stats['3.0%_Target']['losses'] += 1
                stats['3.0%_Target']['pnl_r'] -= 1.0

            # --- 3. Evaluate 5.0% Target ---
            stats['5.0%_Target']['trades'] += 1
            tp_5pct = entry * 1.05 if is_long else entry * 0.95
            sl_5pct = entry - (stop_dist * 2.0) if is_long else entry + (stop_dist * 2.0)
            
            win_5 = False
            for _, fbar in future.iterrows():
                if is_long:
                    if fbar['h'] >= tp_5pct: win_5 = True; break
                    if fbar['l'] <= sl_5pct: break
                else:
                    if fbar['l'] <= tp_5pct: win_5 = True; break
                    if fbar['h'] >= sl_5pct: break
            if win_5:
                stats['5.0%_Target']['wins'] += 1
                stats['5.0%_Target']['pnl_r'] += 5.0
            else:
                stats['5.0%_Target']['losses'] += 1
                stats['5.0%_Target']['pnl_r'] -= 1.0

    print(f"\n{'Target Model':<18} | {'Trades':<8} | {'Wins':<6} | {'Win Rate':<10} | {'Net R Return':<12}")
    print("-" * 75)
    for model, data in stats.items():
        wr = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
        print(f"{model:<18} | {data['trades']:<8} | {data['wins']:<6} | {wr:>6.2f}%    | {data['pnl_r']:>+8.2f}R")
    print("=" * 75)

if __name__ == '__main__':
    run_comparison()
