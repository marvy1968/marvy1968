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
    return np.mean(tr_list[-period:]) if tr_list else 1.0

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
print("🎯 DYNAMIC TRADE MANAGEMENT AUDIT (BREAKEVEN + 20 SMA TRAIL + EOD CLOSE)")
print("=" * 85)

all_bars = {}
for s in symbols:
    b = fetch_bars(s, "5Min", 1000)
    if len(b) >= 55:
        all_bars[s] = b

def run_dynamic_audit(name, target_rr=1.0, move_be_at=0.6, trail_20sma=True):
    total_trades = 0
    total_pnl_r = 0.0
    wins = 0
    scratch = 0
    losses = 0

    for sym, bars in all_bars.items():
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        vols = [b["v"] for b in bars]
        timestamps = [b["t"] for b in bars]

        days = {}
        for idx, t in enumerate(timestamps):
            d = t[:10]
            if d not in days:
                days[d] = []
            days[d].append(idx)

        for d_str, idxs in days.items():
            if len(idxs) < 20:
                continue

            day_vol = 0.0
            day_vwap_num = 0.0

            for k_pos, i in enumerate(idxs):
                typical = (highs[i] + lows[i] + closes[i]) / 3.0
                day_vol += vols[i]
                day_vwap_num += typical * vols[i]
                vwap = day_vwap_num / (day_vol + 1e-5)

                if k_pos < 6 or k_pos > len(idxs) - 4:
                    continue

                if i < 25:
                    continue

                price = closes[i]
                sma20 = np.mean(closes[i-20:i])
                sma20_prev = np.mean(closes[i-25:i-5])
                sma20_slope = (sma20 - sma20_prev) / (sma20_prev + 1e-5)
                atr = calculate_atr(bars[max(0, i-20):i+1], period=14)
                adx = calculate_adx(highs[max(0, i-35):i+1], lows[max(0, i-35):i+1], closes[max(0, i-35):i+1], period=14)

                bar_range = highs[i] - lows[i]
                body_size = abs(closes[i] - opens[i])
                avg_vol = np.mean(vols[max(0, i-20):i])
                rvol = vols[i] / (avg_vol + 1e-5)

                near_20sma = abs(price - sma20) <= (1.2 * atr)

                is_bull = (body_size >= 0.65 * atr and closes[i] > opens[i]) or (bar_range > 0 and (min(opens[i], closes[i]) - lows[i]) / bar_range >= 0.35 and closes[i] > opens[i])
                is_bear = (body_size >= 0.65 * atr and closes[i] < opens[i]) or (bar_range > 0 and (highs[i] - max(opens[i], closes[i])) / bar_range >= 0.35 and closes[i] < opens[i])

                # LONG Trigger
                if price >= vwap and sma20_slope > 0.0001 and near_20sma and adx >= 20.0 and rvol >= 1.0 and is_bull:
                    initial_sl = lows[i] - (0.05 * atr)
                    risk = price - initial_sl
                    if risk <= 0:
                        continue

                    current_sl = initial_sl
                    tp = price + (target_rr * risk)
                    be_level = price + (move_be_at * risk)
                    trade_result_r = None

                    for f in idxs[k_pos+1:]:
                        curr_sma20 = np.mean(closes[max(0, f-20):f])

                        # 1. Target Hit
                        if highs[f] >= tp:
                            trade_result_r = target_rr
                            break

                        # 2. Stop Loss Hit
                        if lows[f] <= current_sl:
                            trade_result_r = (current_sl - price) / risk
                            break

                        # 3. Move to Breakeven
                        if highs[f] >= be_level and current_sl < price:
                            current_sl = price

                        # 4. Trailing 20 SMA Exit (after hitting BE)
                        if trail_20sma and current_sl >= price and closes[f] < curr_sma20:
                            trade_result_r = max(0.0, (closes[f] - price) / risk)
                            break

                    # End of day mark-to-market if unclosed
                    if trade_result_r is None:
                        exit_price = closes[idxs[-1]]
                        trade_result_r = (exit_price - price) / risk

                    total_trades += 1
                    total_pnl_r += trade_result_r
                    if trade_result_r >= 0.5:
                        wins += 1
                    elif abs(trade_result_r) < 0.5:
                        scratch += 1
                    else:
                        losses += 1

                # SHORT Trigger
                elif price <= vwap and sma20_slope < -0.0001 and near_20sma and adx >= 20.0 and rvol >= 1.0 and is_bear:
                    initial_sl = highs[i] + (0.05 * atr)
                    risk = initial_sl - price
                    if risk <= 0:
                        continue

                    current_sl = initial_sl
                    tp = price - (target_rr * risk)
                    be_level = price - (move_be_at * risk)
                    trade_result_r = None

                    for f in idxs[k_pos+1:]:
                        curr_sma20 = np.mean(closes[max(0, f-20):f])

                        if lows[f] <= tp:
                            trade_result_r = target_rr
                            break

                        if highs[f] >= current_sl:
                            trade_result_r = (price - current_sl) / risk
                            break

                        if lows[f] <= be_level and current_sl > price:
                            current_sl = price

                        if trail_20sma and current_sl <= price and closes[f] > curr_sma20:
                            trade_result_r = max(0.0, (price - closes[f]) / risk)
                            break

                    if trade_result_r is None:
                        exit_price = closes[idxs[-1]]
                        trade_result_r = (price - exit_price) / risk

                    total_trades += 1
                    total_pnl_r += trade_result_r
                    if trade_result_r >= 0.5:
                        wins += 1
                    elif abs(trade_result_r) < 0.5:
                        scratch += 1
                    else:
                        losses += 1

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    ev = total_pnl_r / total_trades if total_trades > 0 else 0.0
    print(f"📊 [{name:<30}] Trades: {total_trades:>3} | Wins: {wins:>2} | Scratches: {scratch:>2} | Losses: {losses:>2} | Win Rate: {wr:>4.1f}% | Net EV: {ev:+.3f} R")

print("\n" + "=" * 85)
run_dynamic_audit("1. Fixed Static 1.5R (No BE)", target_rr=1.5, move_be_at=999, trail_20sma=False)
run_dynamic_audit("2. Scalp 1.0R Target + BE @ +0.6R", target_rr=1.0, move_be_at=0.6, trail_20sma=False)
run_dynamic_audit("3. Trend Ride: BE @ +0.6R + 20SMA Trail", target_rr=2.5, move_be_at=0.6, trail_20sma=True)
print("=" * 85)
