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
print("🛡️ RTH INTRADAY SESSION-AWARE AUDIT (NO OVERNIGHT HOLDS + TRAILING STOPS)")
print("=" * 85)

all_bars = {}
for s in symbols:
    b = fetch_bars(s, "5Min", 1000)
    if len(b) >= 55:
        all_bars[s] = b

def run_rth_test(name, strategy_type):
    wins, losses = 0, 0
    
    for sym, bars in all_bars.items():
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        vols = [b["v"] for b in bars]
        timestamps = [b["t"] for b in bars]

        # Group bars by trading day
        days = {}
        for idx, t in enumerate(timestamps):
            day_str = t[:10]
            if day_str not in days:
                days[day_str] = []
            days[day_str].append(idx)

        for day_str, indices in days.items():
            if len(indices) < 20:
                continue

            day_vol = 0
            day_vwap_num = 0

            for k_pos, i in enumerate(indices):
                # Cumulative session VWAP
                typical = (highs[i] + lows[i] + closes[i]) / 3.0
                day_vol += vols[i]
                day_vwap_num += typical * vols[i]
                vwap = day_vwap_num / (day_vol + 1e-5)

                # Skip first 30 mins (9:30-10:00 AM opening chop) & last 15 mins (close out)
                if k_pos < 6 or k_pos > len(indices) - 4:
                    continue

                if i < 20:
                    continue

                price = closes[i]
                sma20 = np.mean(closes[i-20:i])
                sma20_prev = np.mean(closes[i-25:i-5])
                sma20_slope = (sma20 - sma20_prev) / sma20_prev
                atr = calculate_atr(bars[i-20:i+1], period=14)
                adx = calculate_adx(highs[i-35:i+1], lows[i-35:i+1], closes[i-35:i+1], period=14)

                bar_range = highs[i] - lows[i]
                body_size = abs(closes[i] - opens[i])
                avg_vol = np.mean(vols[i-20:i-1])
                rvol = vols[i] / (avg_vol + 1e-5)

                near_20sma = abs(price - sma20) <= (1.2 * atr)

                is_bull_candle = (body_size >= 0.65 * atr and closes[i] > opens[i]) or (bar_range > 0 and (min(opens[i], closes[i]) - lows[i]) / bar_range >= 0.35 and closes[i] > opens[i])
                is_bear_candle = (body_size >= 0.65 * atr and closes[i] < opens[i]) or (bar_range > 0 and (highs[i] - max(opens[i], closes[i])) / bar_range >= 0.35 and closes[i] < opens[i])

                long_trigger = False
                short_trigger = False
                sl, tp = 0, 0

                if strategy_type == "VELEZ_RTH":
                    if price >= vwap and sma20_slope > 0.0001 and near_20sma and adx >= 20.0 and rvol >= 1.0 and is_bull_candle:
                        long_trigger = True
                        sl = lows[i] - (0.05 * atr)
                        risk = price - sl
                        tp = price + (1.5 * risk)
                    elif price <= vwap and sma20_slope < -0.0001 and near_20sma and adx >= 20.0 and rvol >= 1.0 and is_bear_candle:
                        short_trigger = True
                        sl = highs[i] + (0.05 * atr)
                        risk = sl - price
                        tp = price - (1.5 * risk)

                elif strategy_type == "MINERVINI_RTH":
                    pivot_h = max(highs[i-4:i])
                    pivot_l = min(lows[i-4:i])
                    if price >= vwap and rvol >= 1.2 and closes[i] > pivot_h:
                        long_trigger = True
                        sl = pivot_l - (0.05 * atr)
                        risk = price - sl
                        tp = price + (1.5 * risk)
                    elif price <= vwap and rvol >= 1.2 and closes[i] < pivot_l:
                        short_trigger = True
                        sl = pivot_h + (0.05 * atr)
                        risk = sl - price
                        tp = price - (1.5 * risk)

                # Execute intraday only (must resolve before session close)
                if long_trigger and (price - sl) > 0:
                    for f in indices[k_pos+1:]:
                        if lows[f] <= sl:
                            losses += 1
                            break
                        elif highs[f] >= tp:
                            wins += 1
                            break

                elif short_trigger and (sl - price) > 0:
                    for f in indices[k_pos+1:]:
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
run_rth_test("1. Minervini VCP RTH Breakout", "MINERVINI_RTH")
run_rth_test("2. Oliver Velez V2 RTH Engine", "VELEZ_RTH")
print("=" * 85)
