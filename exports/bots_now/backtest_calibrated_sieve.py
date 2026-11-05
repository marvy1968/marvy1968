import os, datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
DATA_URL = "https://data.alpaca.markets/v2"

SECTOR_MAP = {
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK",
    "TSLA": "XLY", "AMD":  "XLK", "CVX":  "XLE"
}

def get_alpaca_bars(symbol, timeframe="15Min", days=60):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    start_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": timeframe, "start": start_dt, "limit": 3000, "adjustment": "raw", "feed": "iex"}
    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200: return None
    bars = r.json().get("bars", [])
    if not bars: return None
    df = pd.DataFrame(bars)
    df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    df['time'] = pd.to_datetime(df['time'])
    return df

def simulate_calibrated_system(df, sector_df=None):
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['vol_sma'] = df['volume'].rolling(20).mean()
    df['sma150'] = df['close'].rolling(150).mean()
    
    # Cumulative VWAP
    df['cum_pv'] = (df['close'] * df['volume']).cumsum()
    df['cum_v'] = df['volume'].cumsum()
    df['vwap'] = df['cum_pv'] / (df['cum_v'] + 1e-9)

    df['swing_high'] = df['high'].rolling(40).max()
    df['swing_low'] = df['low'].rolling(40).min()

    # Sector Filter
    sector_pass = pd.Series(True, index=df.index)
    if sector_df is not None:
        sector_df['ema50'] = sector_df['close'].ewm(span=50, adjust=False).mean()
        aligned = sector_df.set_index('time').reindex(df['time'], method='ffill')
        sector_pass = aligned['close'] > (aligned['ema50'] * 0.985) # Filter out severe sector collapses

    SLIP = 0.03
    SPREAD = 0.02
    
    trades = []
    in_pos = False
    entry_px = 0.0
    initial_risk = 0.0
    virtual_sl = 0.0
    target_tp1 = 0.0
    target_tp2 = 0.0
    tier = 0
    max_fav_px = 0.0
    max_adv_px = 0.0

    for i in range(150, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        atr = row['atr']
        if pd.isna(atr) or atr <= 0: continue

        noise = np.random.normal(0, 0.15 * atr)
        n_low = min(row['low'], row['low'] + noise)

        if in_pos:
            max_fav_px = max(max_fav_px, row['high'])
            max_adv_px = min(max_adv_px, row['low'])
            
            if n_low <= (virtual_sl + SPREAD):
                exit_px = virtual_sl - SLIP
                pnl_r = (exit_px - entry_px) / initial_risk
                mae_r = (entry_px - max_adv_px) / initial_risk
                mfe_r = (max_fav_px - entry_px) / initial_risk
                trades.append({"side": "LONG", "pnl_r": pnl_r, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r)})
                in_pos = False
                continue

            if row['high'] >= target_tp1 and tier < 1:
                virtual_sl = max(virtual_sl, entry_px + (0.05 * atr))
                tier = 1

            if row['high'] >= target_tp2 and tier < 2:
                virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))
                tier = 2
            elif tier == 2:
                virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))

        else:
            s_high = prev['swing_high']
            s_low = prev['swing_low']
            fib_range = s_high - s_low
            if fib_range <= (1.0 * atr): continue

            # Macro Gate
            if not sector_pass.iloc[i] or row['close'] < (row['sma150'] * 0.98):
                continue

            # Confluence Structure
            if row['ema9'] > row['ema21'] and row['close'] > row['bb_mid']:
                fib_382 = s_high - (0.382 * fib_range)
                fib_500 = s_high - (0.500 * fib_range)
                fib_618 = s_high - (0.618 * fib_range)

                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster_window = 0.5 * atr
                best_cluster = max([ [lvl for lvl in candidates if abs(lvl - target) <= cluster_window] for target in candidates ], key=len)

                if (len(best_cluster) / len(candidates)) >= 0.5:
                    buy_zone_max = max(best_cluster) + (0.15 * atr)
                    buy_zone_min = min(best_cluster) - (0.15 * atr)
                    
                    # Soft Score Filter (Need >= 2 of 3)
                    score = 0
                    if row['close'] >= row['vwap']: score += 1
                    if row['volume'] >= (0.85 * row['vol_sma']): score += 1 # Relaxed volume floor
                    if (row['close'] - row['low']) > (0.3 * (row['high'] - row['low'] + 1e-6)): score += 1 # Lower tail wick bounce

                    if (row['low'] <= buy_zone_max) and (row['close'] >= buy_zone_min) and (score >= 2):
                        in_pos = True
                        entry_px = row['close'] + SLIP + SPREAD
                        initial_sl = fib_618 - (0.5 * atr)
                        initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                        virtual_sl = entry_px - initial_risk
                        target_tp1 = min(row['bb_upper'], s_high)
                        target_tp2 = s_high + (0.272 * fib_range)
                        tier = 0
                        max_fav_px = entry_px
                        max_adv_px = entry_px

    return pd.DataFrame(trades)

def get_metrics(trades_df):
    if len(trades_df) == 0: return None
    r = trades_df['pnl_r']
    wins, losses = r[r > 0], r[r <= 0]
    cum_r = r.cumsum()
    return {
        "trades": len(trades_df),
        "win_rate": (len(wins) / len(r)) * 100.0,
        "net_r": r.sum(),
        "pf": abs(wins.sum() / (losses.sum() + 1e-9)),
        "exp": r.mean(),
        "avg_win": wins.mean() if len(wins) > 0 else 0.0,
        "avg_loss": losses.mean() if len(losses) > 0 else 0.0,
        "payoff": abs((wins.mean() if len(wins) > 0 else 0.0) / (losses.mean() if len(losses) > 0 else 1e-9)),
        "max_dd": (cum_r.cummax() - cum_r).max(),
        "mae": trades_df['mae_r'].mean(),
        "mfe": trades_df['mfe_r'].mean()
    }

fleet = ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "CVX"]
all_res = []
for sym in fleet:
    stock_df = get_alpaca_bars(sym, "15Min", days=60)
    sec_df = get_alpaca_bars(SECTOR_MAP.get(sym, "XLK"), "15Min", days=60)
    if stock_df is None: continue
    res = simulate_calibrated_system(stock_df.copy(), sec_df)
    if len(res) > 0: all_res.append(res)

comb = pd.concat(all_res) if all_res else pd.DataFrame()
m = get_metrics(comb)

print("=" * 80)
print(" 🏆 CALIBRATED MULTI-LAYER SIEVE RESULTS (35-45 TRADE TARGET)")
print("=" * 80)
print(f"Total Trades (N)        : {m['trades']}")
print(f"Win Rate (%)            : {m['win_rate']:.1f}%")
print(f"Net Profit (R)          : {m['net_r']:+.2f}R")
print(f"Profit Factor (PF)      : {m['pf']:.2f}")
print(f"Expectancy / Trade      : {m['exp']:+.2f}R")
print(f"Payoff Ratio (Avg W/L)  : {m['payoff']:.2f} (Avg Win: {m['avg_win']:+.2f}R | Avg Loss: {m['avg_loss']:+.2f}R)")
print(f"Max Drawdown (R)        : {m['max_dd']:.2f}R")
print(f"Average MAE / MFE       : MAE: {m['mae']:.2f}R | MFE: {m['mfe']:.2f}R")
print("=" * 80)
