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

def run_backtest(tickers=['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'KO', 'LLY', 'XOM', 'NFLX']):
    print("=" * 80)
    print("🔬 ADVANCED R-MULTIPLE & 70/30 SCALE-OUT RUNNER BACKTEST (30 DAYS)")
    print("=" * 80)
    
    models = {
        '1.25R_Fixed': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0},
        '1.50R_Fixed': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0},
        '2.00R_Fixed': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0},
        '70_30_ScaleOut': {'trades': 0, 'wins': 0, 'losses': 0, 'pnl_r': 0.0}
    }
    
    for sym in tickers:
        df_1h = fetch_bars(sym, '1Hour', limit=500)
        df_1m = fetch_bars(sym, '1Min', limit=5000)
        
        if df_1h.empty or df_1m.empty:
            continue
            
        # 1. HTF Trend (1H)
        df_1h['ema20'] = df_1h['c'].ewm(span=20, adjust=False).mean()
        df_1h['ema50'] = df_1h['c'].ewm(span=50, adjust=False).mean()
        df_1h['trend_bias'] = np.where(df_1h['ema20'] > df_1h['ema50'], 1, -1)
        df_1m['htf_bias'] = df_1h['trend_bias'].reindex(df_1m.index, method='ffill')
        
        # 2. Micro 1M Indicators
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
            future = df_1m.iloc[i+1:i+61]
            
            # --- 1. 1.25R Fixed ---
            models['1.25R_Fixed']['trades'] += 1
            tp_125 = entry + (stop_dist * 1.25) if is_long else entry - (stop_dist * 1.25)
            sl_fixed = entry - stop_dist if is_long else entry + stop_dist
            win_125 = False
            for _, fbar in future.iterrows():
                if is_long:
                    if fbar['h'] >= tp_125: win_125 = True; break
                    if fbar['l'] <= sl_fixed: break
                else:
                    if fbar['l'] <= tp_125: win_125 = True; break
                    if fbar['h'] >= sl_fixed: break
            if win_125:
                models['1.25R_Fixed']['wins'] += 1
                models['1.25R_Fixed']['pnl_r'] += 1.25
            else:
                models['1.25R_Fixed']['losses'] += 1
                models['1.25R_Fixed']['pnl_r'] -= 1.0

            # --- 2. 1.50R Fixed ---
            models['1.50R_Fixed']['trades'] += 1
            tp_150 = entry + (stop_dist * 1.50) if is_long else entry - (stop_dist * 1.50)
            win_150 = False
            for _, fbar in future.iterrows():
                if is_long:
                    if fbar['h'] >= tp_150: win_150 = True; break
                    if fbar['l'] <= sl_fixed: break
                else:
                    if fbar['l'] <= tp_150: win_150 = True; break
                    if fbar['h'] >= sl_fixed: break
            if win_150:
                models['1.50R_Fixed']['wins'] += 1
                models['1.50R_Fixed']['pnl_r'] += 1.50
            else:
                models['1.50R_Fixed']['losses'] += 1
                models['1.50R_Fixed']['pnl_r'] -= 1.0

            # --- 3. 2.00R Fixed ---
            models['2.00R_Fixed']['trades'] += 1
            tp_200 = entry + (stop_dist * 2.00) if is_long else entry - (stop_dist * 2.00)
            win_200 = False
            for _, fbar in future.iterrows():
                if is_long:
                    if fbar['h'] >= tp_200: win_200 = True; break
                    if fbar['l'] <= sl_fixed: break
                else:
                    if fbar['l'] <= tp_200: win_200 = True; break
                    if fbar['h'] >= sl_fixed: break
            if win_200:
                models['2.00R_Fixed']['wins'] += 1
                models['2.00R_Fixed']['pnl_r'] += 2.00
            else:
                models['2.00R_Fixed']['losses'] += 1
                models['2.00R_Fixed']['pnl_r'] -= 1.0

            # --- 4. 70/30 Scale-Out Runner Model ---
            # 70% TP at +0.85R -> Moves SL to Break-Even for remaining 30% runner
            # 30% Runner trails with 1.85 ATR
            models['70_30_ScaleOut']['trades'] += 1
            tp_scale = entry + (stop_dist * 0.85) if is_long else entry - (stop_dist * 0.85)
            t1_hit = False
            trade_pnl = 0.0
            runner_stop = entry - stop_dist if is_long else entry + stop_dist
            runner_exit = None
            
            for _, fbar in future.iterrows():
                # Phase 1: Waiting for T1 (+0.85R)
                if not t1_hit:
                    if is_long:
                        if fbar['h'] >= tp_scale:
                            t1_hit = True
                            runner_stop = entry  # Move stop to Break-Even
                            trade_pnl += 0.70 * 0.85  # Bank 70% at +0.85R
                        elif fbar['l'] <= runner_stop:
                            trade_pnl = -1.00  # Full loss
                            break
                    else:
                        if fbar['l'] <= tp_scale:
                            t1_hit = True
                            runner_stop = entry  # Move stop to Break-Even
                            trade_pnl += 0.70 * 0.85
                        elif fbar['h'] >= runner_stop:
                            trade_pnl = -1.00
                            break
                # Phase 2: Trailing the 30% Runner
                else:
                    cur_atr = fbar['atr'] if fbar['atr'] > 0 else atr_val
                    if is_long:
                        trail_candidate = fbar['h'] - (cur_atr * 1.85)
                        if trail_candidate > runner_stop:
                            runner_stop = trail_candidate
                        if fbar['l'] <= runner_stop:
                            runner_r = (runner_stop - entry) / stop_dist
                            trade_pnl += 0.30 * runner_r
                            runner_exit = True
                            break
                    else:
                        trail_candidate = fbar['l'] + (cur_atr * 1.85)
                        if trail_candidate < runner_stop:
                            runner_stop = trail_candidate
                        if fbar['h'] >= runner_stop:
                            runner_r = (entry - runner_stop) / stop_dist
                            trade_pnl += 0.30 * runner_r
                            runner_exit = True
                            break
                            
            if t1_hit and not runner_exit:
                # Closed at end of 60 bars
                last_price = future.iloc[-1]['c']
                runner_r = (last_price - entry) / stop_dist if is_long else (entry - last_price) / stop_dist
                trade_pnl += 0.30 * runner_r

            models['70_30_ScaleOut']['pnl_r'] += trade_pnl
            if trade_pnl > 0:
                models['70_30_ScaleOut']['wins'] += 1
            else:
                models['70_30_ScaleOut']['losses'] += 1

    print(f"\n{'Target Model':<18} | {'Trades':<8} | {'Wins':<6} | {'Win Rate':<10} | {'Net R Return':<12} | {'Exp / Trade':<10}")
    print("-" * 80)
    for model, data in models.items():
        wr = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
        exp = (data['pnl_r'] / data['trades']) if data['trades'] > 0 else 0
        print(f"{model:<18} | {data['trades']:<8} | {data['wins']:<6} | {wr:>6.2f}%    | {data['pnl_r']:>+8.2f}R   | {exp:>+6.3f}R")
    print("=" * 80)

if __name__ == '__main__':
    run_backtest()
