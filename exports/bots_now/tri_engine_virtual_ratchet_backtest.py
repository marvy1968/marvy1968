import os, sys, datetime, requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv("/home/moonrider1968/.env")

OANDA_KEY = os.getenv("OANDA_API_KEY", os.getenv("OANDA_API_TOKEN"))
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

ALPACA_KEY = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
ALPACA_DATA_URL = "https://data.alpaca.markets"

BASE_RISK = 300.0

now = datetime.datetime.now(datetime.timezone.utc)
start_dt = (now - datetime.timedelta(days=28)).strftime("%Y-%m-%dT%H:%M:%SZ")
end_dt = now.strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_oanda_candles(instrument, count=500):
    url = f"{OANDA_URL}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_KEY}"}
    params = {"granularity": "H1", "count": count, "price": "M"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            candles = r.json().get('candles', [])
            data = [{
                'time': c['time'],
                'open': float(c['mid']['o']),
                'high': float(c['mid']['h']),
                'low': float(c['mid']['l']),
                'close': float(c['mid']['c']),
                'volume': float(c['volume'])
            } for c in candles if c.get('complete', False)]
            return pd.DataFrame(data)
    except Exception as e:
        print(f"OANDA fetch error {instrument}: {e}")
    return None

def fetch_alpaca_crypto_candles(symbol, timeframe="1Hour", limit=500):
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    url = f"{ALPACA_DATA_URL}/v1beta3/crypto/us/bars"
    params = {"symbols": symbol, "timeframe": timeframe, "start": start_dt, "end": end_dt, "limit": limit}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            bars = r.json().get('bars', {}).get(symbol, [])
            data = [{
                'time': b['t'],
                'open': float(b['o']),
                'high': float(b['h']),
                'low': float(b['l']),
                'close': float(b['c']),
                'volume': float(b['v'])
            } for b in bars]
            return pd.DataFrame(data)
    except Exception as e:
        print(f"Alpaca Crypto fetch error {symbol}: {e}")
    return None

def fetch_alpaca_equity_candles(symbol, timeframe="15Min", limit=500):
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    url = f"{ALPACA_DATA_URL}/v2/stocks/bars"
    params = {"symbols": symbol, "timeframe": timeframe, "start": start_dt, "end": end_dt, "limit": limit, "feed": "iex"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            bars = r.json().get('bars', {}).get(symbol, [])
            data = [{
                'time': b['t'],
                'open': float(b['o']),
                'high': float(b['h']),
                'low': float(b['l']),
                'close': float(b['c']),
                'volume': float(b['v'])
            } for b in bars]
            return pd.DataFrame(data)
    except Exception as e:
        print(f"Alpaca Equity fetch error {symbol}: {e}")
    return None

def simulate_strategy(df, asset_type="FOREX"):
    if df is None or len(df) < 55:
        return None

    df = df.copy()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))

    df['donchian_high'] = df['high'].shift(1).rolling(20 if asset_type == "EQUITY" else 55).max()
    df['donchian_low'] = df['low'].shift(1).rolling(20 if asset_type == "EQUITY" else 55).min()

    results = {
        "FIXED": {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0},
        "VIRTUAL_RATCHET": {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0}
    }

    for mode in ["FIXED", "VIRTUAL_RATCHET"]:
        pos = None
        for i in range(55, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i-1]

            buy_sig = False
            sell_sig = False

            if asset_type == "FOREX":
                if (prev['low'] <= prev['bb_lower']) and (row['close'] > row['bb_lower']) and (row['rsi'] < 40):
                    buy_sig = True
                elif (prev['high'] >= prev['bb_upper']) and (row['close'] < row['bb_upper']) and (row['rsi'] > 60):
                    sell_sig = True
            else:
                if row['close'] > prev['donchian_high'] and row['close'] > row['ema21']:
                    buy_sig = True
                elif row['close'] < prev['donchian_low'] and row['close'] < row['ema21']:
                    sell_sig = True

            if pos is not None:
                entry_px = pos['entry']
                direction = pos['direction']
                init_risk = pos['risk_dist']
                units = pos['units']
                cur_close = row['close']
                bb_mid = row['bb_mid']
                ema21 = row['ema21']
                atr = row['atr']

                mfe = (cur_close - entry_px) if direction == 'LONG' else (entry_px - cur_close)
                r_mult = mfe / (init_risk + 1e-9)

                trade_closed = False
                exit_pnl = 0.0

                if mode == "FIXED":
                    if direction == 'LONG':
                        if row['low'] <= pos['sl']:
                            trade_closed = True
                            exit_pnl = -BASE_RISK
                        elif row['high'] >= pos['tp']:
                            trade_closed = True
                            exit_pnl = BASE_RISK * 1.5
                    else:
                        if row['high'] >= pos['sl']:
                            trade_closed = True
                            exit_pnl = -BASE_RISK
                        elif row['low'] <= pos['tp']:
                            trade_closed = True
                            exit_pnl = BASE_RISK * 1.5

                elif mode == "VIRTUAL_RATCHET":
                    if direction == 'LONG':
                        if cur_close <= pos['virtual_sl']:
                            trade_closed = True
                            exit_pnl = (pos['virtual_sl'] - entry_px) * units
                        elif asset_type == "FOREX" and cur_close >= bb_mid and mfe > 0:
                            trade_closed = True
                            exit_pnl = (cur_close - entry_px) * units
                        else:
                            if r_mult >= 2.0:
                                pos['virtual_sl'] = max(pos['virtual_sl'], ema21 - (0.5 * atr))
                            elif r_mult >= 1.2:
                                pos['virtual_sl'] = max(pos['virtual_sl'], entry_px + (0.2 * atr))
                    else:
                        if cur_close >= pos['virtual_sl']:
                            trade_closed = True
                            exit_pnl = (entry_px - pos['virtual_sl']) * units
                        elif asset_type == "FOREX" and cur_close <= bb_mid and mfe > 0:
                            trade_closed = True
                            exit_pnl = (entry_px - cur_close) * units
                        else:
                            if r_mult >= 2.0:
                                pos['virtual_sl'] = min(pos['virtual_sl'], ema21 + (0.5 * atr))
                            elif r_mult >= 1.2:
                                pos['virtual_sl'] = min(pos['virtual_sl'], entry_px - (0.2 * atr))

                if trade_closed:
                    results[mode]['pnl'] += exit_pnl
                    results[mode]['trades'] += 1
                    if exit_pnl > 0:
                        results[mode]['wins'] += 1
                    else:
                        results[mode]['losses'] += 1
                    pos = None

            if pos is None and (buy_sig or sell_sig):
                direction = 'LONG' if buy_sig else 'SHORT'
                entry_px = row['close']
                risk_dist = max(1.5 * row['atr'], entry_px * 0.005)
                units = BASE_RISK / risk_dist

                pos = {
                    "direction": direction,
                    "entry": entry_px,
                    "risk_dist": risk_dist,
                    "units": units,
                    "sl": entry_px - risk_dist if direction == 'LONG' else entry_px + risk_dist,
                    "tp": entry_px + (1.5 * risk_dist) if direction == 'LONG' else entry_px - (1.5 * risk_dist),
                    "virtual_sl": entry_px - risk_dist if direction == 'LONG' else entry_px + risk_dist
                }

    return results

print("=========================================================================================")
print("  🚀 FULL FLEET TRI-ENGINE BACKTEST: FIXED TARGETS VS. VIRTUAL RATCHET TP                ")
print("=========================================================================================")

fleets = [
    ("FOREX (OANDA H1)", "USD_CAD", "FOREX", fetch_oanda_candles("USD_CAD", 500)),
    ("FOREX (OANDA H1)", "USD_JPY", "FOREX", fetch_oanda_candles("USD_JPY", 500)),
    ("CRYPTO (Alpaca H1)", "ETH/USD", "CRYPTO", fetch_alpaca_crypto_candles("ETH/USD", "1Hour", 500)),
    ("CRYPTO (Alpaca H1)", "BTC/USD", "CRYPTO", fetch_alpaca_crypto_candles("BTC/USD", "1Hour", 500)),
    ("EQUITIES (Alpaca 15m)", "NVDA", "EQUITY", fetch_alpaca_equity_candles("NVDA", "15Min", 500)),
    ("EQUITIES (Alpaca 15m)", "AAPL", "EQUITY", fetch_alpaca_equity_candles("AAPL", "15Min", 500))
]

summary_rows = []

for engine, symbol, asset_type, df in fleets:
    res = simulate_strategy(df, asset_type)
    if not res:
        print(f"⚠️ Insufficient data for {symbol}")
        continue

    f_pnl = res['FIXED']['pnl']
    f_trades = res['FIXED']['trades']
    f_wr = (res['FIXED']['wins'] / f_trades * 100) if f_trades > 0 else 0.0

    r_pnl = res['VIRTUAL_RATCHET']['pnl']
    r_trades = res['VIRTUAL_RATCHET']['trades']
    r_wr = (res['VIRTUAL_RATCHET']['wins'] / r_trades * 100) if r_trades > 0 else 0.0
    diff = r_pnl - f_pnl

    summary_rows.append({
        "Engine": engine,
        "Asset": symbol,
        "Fixed PnL": f"${f_pnl:+,.2f}",
        "Fixed Win%": f"{f_wr:.1f}% ({f_trades})",
        "Ratchet PnL": f"${r_pnl:+,.2f}",
        "Ratchet Win%": f"{r_wr:.1f}% ({r_trades})",
        "Edge (Δ PnL)": f"${diff:+,.2f}"
    })

sum_df = pd.DataFrame(summary_rows)
print("\n" + sum_df.to_string(index=False))
print("=========================================================================================\n")
