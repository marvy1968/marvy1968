import os
import requests
import numpy as np
from datetime import datetime, timedelta, timezone

APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "REDACTED_BROKER_KEY").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "EnXL5FZSVSQzHvVTGLh1venMboWHXhGeQ2Kgy2gjzX5e").strip()
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

def fetch_bars(symbol, timeframe="5Min", limit=1000):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    start_date = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": timeframe, "limit": limit, "start": start_date}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("bars", []) or []
    except Exception:
        pass
    return []

def calculate_vwap(bars_slice):
    vols = np.array([b["v"] for b in bars_slice])
    highs = np.array([b["h"] for b in bars_slice])
    lows = np.array([b["l"] for b in bars_slice])
    closes = np.array([b["c"] for b in bars_slice])
    typical_prices = (highs + lows + closes) / 3.0
    total_vol = np.sum(vols)
    if total_vol == 0:
        return closes[-1]
    return np.sum(typical_prices * vols) / total_vol

def calculate_atr(bars_slice, period=14):
    if len(bars_slice) < period + 1:
        return bars_slice[-1]["h"] - bars_slice[-1]["l"]
    tr_list = []
    for j in range(1, len(bars_slice)):
        h = bars_slice[j]["h"]
        l = bars_slice[j]["l"]
        cp = bars_slice[j-1]["c"]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        tr_list.append(tr)
    return np.mean(tr_list[-period:])

def calculate_adx(highs_slice, lows_slice, closes_slice, period=14):
    if len(closes_slice) < (period * 2):
        return 0.0
    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, len(closes_slice)):
        h, l = highs_slice[i], lows_slice[i]
        prev_c, prev_h, prev_l = closes_slice[i-1], highs_slice[i-1], lows_slice[i-1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
        up_move = h - prev_h
        down_move = prev_l - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
    if len(tr_list) < period:
        return 0.0
    atr_s = sum(tr_list[:period])
    p_di_s = sum(plus_dm_list[:period])
    m_di_s = sum(minus_dm_list[:period])
    dx_list = []
    for i in range(period, len(tr_list)):
        atr_s = atr_s - (atr_s / period) + tr_list[i]
        p_di_s = p_di_s - (p_di_s / period) + plus_dm_list[i]
        m_di_s = m_di_s - (m_di_s / period) + minus_dm_list[i]
        p_di = 100 * (p_di_s / atr_s) if atr_s != 0 else 0
        m_di = 100 * (m_di_s / atr_s) if atr_s != 0 else 0
        di_sum = p_di + m_di
        dx = 100 * (abs(p_di - m_di) / di_sum) if di_sum != 0 else 0
        dx_list.append(dx)
    return sum(dx_list[-period:]) / period if len(dx_list) >= period else 0.0

symbols = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
print("=" * 85)
print("🏆 HISTORICAL MULTI-ENGINE AUDIT (FULL 8,000-BAR DATASET)")
print("=" * 85)

# Pre-load data
all_bars = {}
for s in symbols:
    b = fetch_bars(s, "5Min", 1000)
    if len(b) >= 55:
        all_bars[s] = b
    print(f"📦 Loaded {s:<5} : {len(b)} bars")

def run_test(name, strategy_type):
    wins, losses = 0, 0
    for sym, bars in all_bars.items():
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        vols = [b["v"] for b in bars]

        for i in range(50, len(bars) - 12):
            price = closes[i]
            sma50 = np.mean(closes[i-50:i])
            sma20 = np.mean(closes[i-20:i])
            sma20_prev = np.mean(closes[i-25:i-5])
            sma20_slope = (sma20 - sma20_prev) / sma20_prev
            vwap = calculate_vwap(bars[max(0, i-50):i+1])
            atr = calculate_atr(bars[i-20:i+1], period=14)
            adx = calculate_adx(highs[i-35:i+1], lows[i-35:i+1], closes[i-35:i+1], period=14)

            bar_range = highs[i] - lows[i]
            body_size = abs(closes[i] - opens[i])
            avg_vol = np.mean(vols[i-20:i-1])
            rvol = vols[i] / (avg_vol + 1e-5)

            near_20sma = abs(price - sma20) <= (1.2 * atr)
            ma_long = (sma20 > sma50) and (price > sma50)
            ma_short = (sma20 < sma50) and (price < sma50)

            is_bull_candle = (body_size >= 0.7 * atr and closes[i] > opens[i]) or (bar_range > 0 and (min(opens[i], closes[i]) - lows[i]) / bar_range >= 0.35 and closes[i] > opens[i])
            is_bear_candle = (body_size >= 0.7 * atr and closes[i] < opens[i]) or (bar_range > 0 and (highs[i] - max(opens[i], closes[i])) / bar_range >= 0.35 and closes[i] < opens[i])

            long_trigger = False
            short_trigger = False
            sl, tp = 0, 0

            # 1. Oliver Velez V2 Engine
            if strategy_type == "VELEZ_V2":
                if ma_long and price >= vwap and sma20_slope > 0.0001 and near_20sma and adx >= 20.0 and rvol >= 1.05 and is_bull_candle:
                    long_trigger = True
                    sl = lows[i] - (0.1 * atr)
                    tp = price + (1.5 * (price - sl))
                elif ma_short and price <= vwap and sma20_slope < -0.0001 and near_20sma and adx >= 20.0 and rvol >= 1.05 and is_bear_candle:
                    short_trigger = True
                    sl = highs[i] + (0.1 * atr)
                    tp = price - (1.5 * (sl - price))

            # 2. Minervini VCP Breakout Engine
            elif strategy_type == "MINERVINI_VCP":
                t1 = max(highs[i-8:i-4]) - min(lows[i-8:i-4])
                t2 = max(highs[i-4:i]) - min(lows[i-4:i])
                pivot_h = max(highs[i-4:i])
                pivot_l = min(lows[i-4:i])
                contracted = (t2 < 0.8 * t1) and (t2 <= 1.5 * atr)
                if ma_long and price >= vwap and contracted and rvol >= 1.25 and closes[i] > pivot_h:
                    long_trigger = True
                    sl = pivot_l - (0.05 * atr)
                    tp = price + (1.5 * (price - sl))
                elif ma_short and price <= vwap and contracted and rvol >= 1.25 and closes[i] < pivot_l:
                    short_trigger = True
                    sl = pivot_h + (0.05 * atr)
                    tp = price - (1.5 * (sl - price))

            # 3. Hybrid: VCP Coiling + Velez 20 SMA Ignition
            elif strategy_type == "HYBRID_VCP_VELEZ":
                prev_ranges = [highs[k] - lows[k] for k in range(i-3, i)]
                is_coiled = np.mean(prev_ranges) <= (1.1 * atr)
                if ma_long and is_coiled and price >= vwap and sma20_slope > 0.0001 and near_20sma and rvol >= 1.0 and is_bull_candle:
                    long_trigger = True
                    sl = min(lows[i-3:i+1]) - (0.05 * atr)
                    tp = price + (1.5 * (price - sl))
                elif ma_short and is_coiled and price <= vwap and sma20_slope < -0.0001 and near_20sma and rvol >= 1.0 and is_bear_candle:
                    short_trigger = True
                    sl = max(highs[i-3:i+1]) + (0.05 * atr)
                    tp = price - (1.5 * (sl - price))

            if long_trigger and (price - sl) > 0:
                for f in range(i + 1, i + 13):
                    if lows[f] <= sl:
                        losses += 1
                        break
                    elif highs[f] >= tp:
                        wins += 1
                        break

            elif short_trigger and (sl - price) > 0:
                for f in range(i + 1, i + 13):
                    if highs[f] >= sl:
                        losses += 1
                        break
                    elif lows[f] <= tp:
                        wins += 1
                        break

    tot = wins + losses
    wr = (wins / tot * 100) if tot > 0 else 0.0
    ev = ((wr / 100) * 1.5) - ((1 - wr / 100) * 1.0) if tot > 0 else 0.0
    print(f"📊 [{name:<28}] Trades: {tot:>4} | Wins: {wins:>3} | Losses: {losses:>3} | Win Rate: {wr:>5.1f}% | Net EV: {ev:+.3f} R")

print("\n" + "=" * 85)
run_test("1. Unconstrained Baseline", "NONE")
run_test("2. Minervini VCP Breakout", "MINERVINI_VCP")
run_test("3. Hybrid VCP + Velez Ignition", "HYBRID_VCP_VELEZ")
run_test("4. Oliver Velez V2 (Live)", "VELEZ_V2")
print("=" * 85)
