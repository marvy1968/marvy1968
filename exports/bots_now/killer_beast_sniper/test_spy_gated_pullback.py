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

symbols = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
print("=" * 85)
print("🏛️ SPY-GATED FIRST-PULLBACK ENGINE AUDIT (REGIME FILTER + FIRST TOUCH)")
print("=" * 85)

spy_bars = fetch_bars("SPY", "5Min", 1000)
spy_dict = {b["t"]: b for b in spy_bars}

all_bars = {}
for s in symbols:
    b = fetch_bars(s, "5Min", 1000)
    if len(b) >= 55:
        all_bars[s] = b
    print(f"📦 Loaded {s:<5} : {len(b)} bars")

def run_spy_gated_audit(target_rr=1.5, move_be_at=0.75):
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
                if i < 30:
                    continue

                ts = timestamps[i]
                if ts not in spy_dict:
                    continue

                price = closes[i]
                sma20 = np.mean(closes[i-20:i])
                sma50 = np.mean(closes[i-50:i])
                atr = calculate_atr(bars[max(0, i-20):i+1], period=14)

                # SPY Regime Check
                spy_idx = [idx for idx, b in enumerate(spy_bars) if b["t"] == ts]
                if not spy_idx:
                    continue
                s_i = spy_idx[0]
                if s_i < 20:
                    continue

                spy_c = spy_bars[s_i]["c"]
                spy_sma20 = np.mean([b["c"] for b in spy_bars[s_i-20:s_i]])
                spy_bull = spy_c >= spy_sma20
                spy_bear = spy_c <= spy_sma20

                # First Pullback Detection: Price crossed above 20 SMA recently (between 2 to 7 bars ago)
                crossed_above_recently = any(closes[m] < np.mean(closes[m-20:m]) for m in range(i-7, i-2))
                crossed_below_recently = any(closes[m] > np.mean(closes[m-20:m]) for m in range(i-7, i-2))

                near_20sma = abs(price - sma20) <= (1.0 * atr)
                body_size = abs(closes[i] - opens[i])
                bar_range = highs[i] - lows[i]

                is_bull_trigger = (body_size >= 0.6 * atr and closes[i] > opens[i]) or (bar_range > 0 and (min(opens[i], closes[i]) - lows[i]) / bar_range >= 0.35 and closes[i] > opens[i])
                is_bear_trigger = (body_size >= 0.6 * atr and closes[i] < opens[i]) or (bar_range > 0 and (highs[i] - max(opens[i], closes[i])) / bar_range >= 0.35 and closes[i] < opens[i])

                # LONG: SPY Bull + Fresh 20 SMA Crossing + Velez Bounce
                if spy_bull and (price >= vwap) and (sma20 > sma50) and crossed_above_recently and near_20sma and is_bull_trigger:
                    sl = lows[i] - (0.05 * atr)
                    risk = price - sl
                    if risk <= 0:
                        continue

                    current_sl = sl
                    tp = price + (target_rr * risk)
                    be_level = price + (move_be_at * risk)
                    res_r = None

                    for f in idxs[k_pos+1:]:
                        if highs[f] >= tp:
                            res_r = target_rr
                            break
                        if lows[f] <= current_sl:
                            res_r = (current_sl - price) / risk
                            break
                        if highs[f] >= be_level and current_sl < price:
                            current_sl = price

                    if res_r is None:
                        res_r = (closes[idxs[-1]] - price) / risk

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r >= 0.5:
                        wins += 1
                    elif abs(res_r) < 0.5:
                        scratch += 1
                    else:
                        losses += 1

                # SHORT: SPY Bear + Fresh 20 SMA Breakdown + Velez Rejection
                elif spy_bear and (price <= vwap) and (sma20 < sma50) and crossed_below_recently and near_20sma and is_bear_trigger:
                    sl = highs[i] + (0.05 * atr)
                    risk = sl - price
                    if risk <= 0:
                        continue

                    current_sl = sl
                    tp = price - (target_rr * risk)
                    be_level = price - (move_be_at * risk)
                    res_r = None

                    for f in idxs[k_pos+1:]:
                        if lows[f] <= tp:
                            res_r = target_rr
                            break
                        if highs[f] >= current_sl:
                            res_r = (price - current_sl) / risk
                            break
                        if lows[f] <= be_level and current_sl > price:
                            current_sl = price

                    if res_r is None:
                        res_r = (price - closes[idxs[-1]]) / risk

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r >= 0.5:
                        wins += 1
                    elif abs(res_r) < 0.5:
                        scratch += 1
                    else:
                        losses += 1

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    ev = total_pnl_r / total_trades if total_trades > 0 else 0.0
    print(f"📊 Trades: {total_trades:>3} | Wins: {wins:>2} | Scratches: {scratch:>2} | Losses: {losses:>2} | Win Rate: {wr:>4.1f}% | Net EV: {ev:+.3f} R")

run_spy_gated_audit(target_rr=1.5, move_be_at=0.75)
print("=" * 85)
