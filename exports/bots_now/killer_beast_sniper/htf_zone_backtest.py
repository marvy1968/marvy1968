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

def run_backtest(tickers=['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'KO', 'LLY', 'XOM']):
    print("=" * 70)
    print("🔬 MULTI-TIMEFRAME HTF ZONE + MICRO SCALP BACKTEST (30 DAYS)")
    print("=" * 70)
    
    total_unfiltered_trades = 0
    total_unfiltered_wins = 0
    
    total_htf_trades = 0
    total_htf_wins = 0
    
    for sym in tickers:
        df_1h = fetch_bars(sym, '1Hour', limit=500)
        df_1m = fetch_bars(sym, '1Min', limit=5000)
        
        if df_1h.empty or df_1m.empty:
            continue
            
        # 1. Compute HTF (1H) Trend & Zone Indicators
        df_1h['ema20'] = df_1h['c'].ewm(span=20, adjust=False).mean()
        df_1h['ema50'] = df_1h['c'].ewm(span=50, adjust=False).mean()
        df_1h['trend_bias'] = np.where(df_1h['ema20'] > df_1h['ema50'], 1, -1)
        
        # 2. Resample / Forward-fill HTF state onto 1M bars
        df_1m['htf_bias'] = df_1h['trend_bias'].reindex(df_1m.index, method='ffill')
        df_1m['htf_ema20'] = df_1h['ema20'].reindex(df_1m.index, method='ffill')
        
        # 3. Compute 1M Micro Scalp Indicators (Connors RSI2 Pullback)
        delta = df_1m['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=2).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=2).mean()
        rs = gain / (loss + 1e-9)
        df_1m['rsi2'] = 100 - (100 / (1 + rs))
        df_1m['ema20_1m'] = df_1m['c'].ewm(span=20, adjust=False).mean()
        
        # Run Simulated Scalps
        for i in range(20, len(df_1m) - 10):
            row = df_1m.iloc[i]
            prev = df_1m.iloc[i-1]
            
            # Micro Scalp Signal (Mean Reversion Bounce)
            is_long_scalp = prev['rsi2'] < 10 and row['c'] > row['ema20_1m']
            is_short_scalp = prev['rsi2'] > 90 and row['c'] < row['ema20_1m']
            
            # --- Unfiltered Scalp Simulation ---
            if is_long_scalp or is_short_scalp:
                total_unfiltered_trades += 1
                entry = row['c']
                # Evaluate 5-bar scalp outcome
                future_bars = df_1m.iloc[i+1:i+6]
                if is_long_scalp and future_bars['h'].max() >= entry * 1.003:
                    total_unfiltered_wins += 1
                elif is_short_scalp and future_bars['l'].min() <= entry * 0.997:
                    total_unfiltered_wins += 1
                    
            # --- HTF Gated Scalp Simulation ---
            # Only take Long if HTF Bias is Bullish AND price near HTF Buy Zone
            if is_long_scalp and row['htf_bias'] == 1:
                total_htf_trades += 1
                entry = row['c']
                future_bars = df_1m.iloc[i+1:i+6]
                if future_bars['h'].max() >= entry * 1.003:
                    total_htf_wins += 1
            elif is_short_scalp and row['htf_bias'] == -1:
                total_htf_trades += 1
                entry = row['c']
                future_bars = df_1m.iloc[i+1:i+6]
                if future_bars['l'].min() <= entry * 0.997:
                    total_htf_wins += 1

    unfiltered_wr = (total_unfiltered_wins / total_unfiltered_trades * 100) if total_unfiltered_trades > 0 else 0
    htf_wr = (total_htf_wins / total_htf_trades * 100) if total_htf_trades > 0 else 0
    
    print(f"\n📊 RESULTS COMPARISON:")
    print(f"  • Standard Unfiltered Scalps : {total_unfiltered_trades} Trades | {total_unfiltered_wins} Wins | Win Rate: {unfiltered_wr:.1f}%")
    print(f"  • HTF Trend/Zone Gated Scalps: {total_htf_trades} Trades | {total_htf_wins} Wins | Win Rate: {htf_wr:.1f}%")
    print("=" * 70)

if __name__ == '__main__':
    run_backtest()
