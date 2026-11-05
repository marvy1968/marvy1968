import os
import sys
import requests
import numpy as np
from datetime import datetime, timedelta, timezone

# Load credentials
env_path = "/home/moonrider1968/bots/killer_beast_sniper/.env"
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

API_KEY = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

SECTORS = {
    "TECH": ["NVDA", "AAPL", "MSFT", "AMD", "AVGO", "QCOM", "INTC", "MU", "AMAT", "TSM"],
    "COMM": ["META", "GOOGL", "NFLX", "DIS", "SPOT", "TMUS", "CMCSA", "RBLX", "SNAP", "PINS"],
    "DISC": ["AMZN", "TSLA", "BABA", "NKE", "SBUX", "HD", "MCD", "LULU", "BKNG", "ABNB"],
    "FIN":  ["JPM", "BAC", "GS", "MS", "WFC", "V", "MA", "COIN", "HOOD", "SOFI"],
    "HEALTH":["LLY", "NVO", "UNH", "JNJ", "ABBV", "MRK", "PFE", "ISRG", "BMY", "MRNA"],
    "IND_NRG":["XOM", "CVX", "COP", "SLB", "CAT", "GE", "BA", "UNP", "HON", "LMT"]
}

ALL_TICKERS = [t for group in SECTORS.values() for t in group]

def fetch_alpaca_bars(symbol):
    # Fetch 5m bars without restrictive feed filtering
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"timeframe": "5Min", "limit": 1000, "adjustment": "all"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=8)
        if r.status_code == 200:
            raw_bars = r.json().get("bars", [])
            bars = []
            for b in raw_bars:
                bars.append({
                    "t": b["t"], "o": float(b["o"]), "h": float(b["h"]),
                    "l": float(b["l"]), "c": float(b["c"]), "v": int(b["v"])
                })
            if len(bars) >= 50:
                return bars
    except Exception:
        pass
    
    # Fallback to yfinance if Alpaca data API throttles
    try:
        import yfinance as yf
        df = yf.download(symbol, period="5d", interval="5m", progress=False)
        if not df.empty:
            bars = []
            for idx, row in df.iterrows():
                bars.append({
                    "t": str(idx),
                    "o": float(row["Open"]),
                    "h": float(row["High"]),
                    "l": float(row["Low"]),
                    "c": float(row["Close"]),
                    "v": int(row["Volume"])
                })
            return bars
    except Exception:
        pass
    return []

def calculate_atr(bars_slice, period=14):
    if len(bars_slice) < period + 1:
        return bars_slice[-1]["h"] - bars_slice[-1]["l"]
    tr_list = []
    for j in range(1, len(bars_slice)):
        h, l, cp = bars_slice[j]["h"], bars_slice[j]["l"], bars_slice[j-1]["c"]
        tr_list.append(max(h - l, abs(h - cp), abs(l - cp)))
    return np.mean(tr_list[-period:]) if tr_list else 0.50

def calculate_rsi(closes, period=2):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

print("=" * 95)
print("📊 BENCHMARK AUDIT: LONGS VS. SHORTS ACROSS 60 MULTI-SECTOR STOCKS (5M BARS)")
print("=" * 95)

all_stock_data = {}
for ticker in ALL_TICKERS:
    b = fetch_alpaca_bars(ticker)
    if len(b) >= 55:
        all_stock_data[ticker] = b
    print(f"📦 Loaded {ticker:<6} : {len(b)} 5m bars")

print(f"\n📦 Successfully loaded bars for {len(all_stock_data)} / {len(ALL_TICKERS)} tickers.\n")

def run_test(name, strategy_type="VCP", side_mode="LONG", target_rr=0.75, move_be_at=0.40):
    total_trades, wins, losses = 0, 0, 0
    total_pnl_r = 0.0

    for ticker, bars in all_stock_data.items():
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]

        for i in range(50, len(bars) - 15):
            price = closes[i]
            sma20 = np.mean(closes[i-20:i])
            sma50 = np.mean(closes[i-50:i])
            atr = calculate_atr(bars[i-20:i+1], period=14)
            rsi2 = calculate_rsi(closes[i-10:i+1], period=2)

            prev_ranges = [highs[k] - lows[k] for k in range(i-4, i-1)]
            is_coiled = np.mean(prev_ranges) <= (1.1 * atr)

            trigger = False
            sl = 0

            # 1. MINERVINI VCP + CONNORS RSI(2)
            if strategy_type == "VCP":
                if side_mode == "LONG":
                    if is_coiled and (price > sma50) and (rsi2 <= 12.0) and (closes[i] > opens[i]):
                        trigger = True
                        sl = min(lows[i-3:i+1]) - (0.05 * atr)
                elif side_mode == "SHORT":
                    if is_coiled and (price < sma50) and (rsi2 >= 88.0) and (closes[i] < opens[i]):
                        trigger = True
                        sl = max(highs[i-3:i+1]) + (0.05 * atr)

            # 2. CONNORS RSI(2) TREND REVERSION
            elif strategy_type == "CONNORS_TREND":
                if side_mode == "LONG":
                    if (price > sma50) and (sma20 > sma50) and (rsi2 <= 10.0):
                        trigger = True
                        sl = min(lows[i-2:i+1]) - (0.05 * atr)
                elif side_mode == "SHORT":
                    if (price < sma50) and (sma20 < sma50) and (rsi2 >= 90.0):
                        trigger = True
                        sl = max(highs[i-2:i+1]) + (0.05 * atr)

            # Execution Engine (+0.75R Target, +0.40R Breakeven)
            if trigger:
                if side_mode == "LONG" and (price - sl) > 0:
                    risk = price - sl
                    current_sl = sl
                    tp = price + (target_rr * risk)
                    be_lvl = price + (move_be_at * risk)
                    res_r = None

                    for f in range(i + 1, min(i + 15, len(bars))):
                        if highs[f] >= tp:
                            res_r = target_rr
                            break
                        if lows[f] <= current_sl:
                            res_r = (current_sl - price) / risk
                            break
                        if highs[f] >= be_lvl and current_sl < price:
                            current_sl = price
                    if res_r is None:
                        res_r = (closes[min(i + 14, len(bars) - 1)] - price) / risk

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r > 0: wins += 1
                    else: losses += 1

                elif side_mode == "SHORT" and (sl - price) > 0:
                    risk = sl - price
                    current_sl = sl
                    tp = price - (target_rr * risk)
                    be_lvl = price - (move_be_at * risk)
                    res_r = None

                    for f in range(i + 1, min(i + 15, len(bars))):
                        if lows[f] <= tp:
                            res_r = target_rr
                            break
                        if highs[f] >= current_sl:
                            res_r = (price - current_sl) / risk
                            break
                        if lows[f] <= be_lvl and current_sl > price:
                            current_sl = price
                    if res_r is None:
                        res_r = (price - closes[min(i + 14, len(bars) - 1)]) / risk

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r > 0: wins += 1
                    else: losses += 1

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    ev = (total_pnl_r / total_trades) if total_trades > 0 else 0.0
    print(f"📊 [{name:<34}] Trades: {total_trades:>4} | Wins: {wins:>3} | Losses: {losses:>3} | Win Rate: {wr:>5.1f}% | Net EV: {ev:+.3f} R")

print("--- SECTOR BENCHMARK: LONGS VS. SHORTS ---")
run_test("1. Minervini VCP (LONG ONLY)", strategy_type="VCP", side_mode="LONG")
run_test("2. Minervini VCP (SHORT ONLY)", strategy_type="VCP", side_mode="SHORT")
run_test("3. Connors RSI2 (LONG ONLY)", strategy_type="CONNORS_TREND", side_mode="LONG")
run_test("4. Connors RSI2 (SHORT ONLY)", strategy_type="CONNORS_TREND", side_mode="SHORT")
print("=" * 95)
