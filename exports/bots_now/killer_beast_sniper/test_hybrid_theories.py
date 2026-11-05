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

def is_valid_tod(timestamp_str):
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        hour_est = (dt.hour - 4) % 24
        minute = dt.minute
        time_decimal = hour_est + (minute / 60.0)
        in_morning = (10.0 <= time_decimal <= 11.5)
        in_afternoon = (13.5 <= time_decimal <= 15.5)
        return in_morning or in_afternoon
    except Exception:
        return True

symbols = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
print("=" * 95)
print("🏆 HYBRID THEORIES BENCHMARK AUDIT (FULL 8,000 HISTORICAL 5M BARS)")
print("=" * 95)

spy_bars = fetch_bars("SPY", "5Min", 1000)
spy_dict = {b["t"]: b for b in spy_bars}

all_bars = {}
for s in symbols:
    b = fetch_bars(s, "5Min", 1000)
    if len(b) >= 55:
        all_bars[s] = b
    print(f"📦 Loaded {s:<5} : {len(b)} bars")

def run_hybrid_theory(name, theory_type, target_rr=0.75, move_be_at=0.40):
    total_trades = 0
    total_pnl_r = 0.0
    wins = 0
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

                if k_pos < 6 or k_pos > len(idxs) - 4 or i < 35:
                    continue

                ts = timestamps[i]
                if ts not in spy_dict or not is_valid_tod(ts):
                    continue

                price = closes[i]
                sma20 = np.mean(closes[i-20:i])
                sma50 = np.mean(closes[i-50:i])
                atr = calculate_atr(bars[max(0, i-20):i+1], period=14)
                rsi2 = calculate_rsi(closes[max(0, i-10):i+1], period=2)
                body_size = abs(closes[i] - opens[i])
                bar_range = highs[i] - lows[i]

                # SPY Regime
                spy_idx = [idx for idx, b in enumerate(spy_bars) if b["t"] == ts]
                if not spy_idx or spy_idx[0] < 20:
                    continue
                s_i = spy_idx[0]
                spy_c = spy_bars[s_i]["c"]
                spy_sma20 = np.mean([b["c"] for b in spy_bars[s_i-20:s_i]])
                spy_bull = spy_c >= spy_sma20
                spy_bear = spy_c <= spy_sma20

                long_trigger = False
                short_trigger = False
                sl = 0

                # -------------------------------------------------------------
                # 1. VELEZ 20 SMA + CONNORS RSI(2) HYBRID
                # -------------------------------------------------------------
                if theory_type == "VELEZ_CONNORS":
                    near_20sma = abs(price - sma20) <= (1.0 * atr)
                    if spy_bull and price >= vwap and sma20 > sma50 and near_20sma and rsi2 <= 10.0:
                        long_trigger = True
                        sl = min(lows[i-2:i+1]) - (0.05 * atr)
                    elif spy_bear and price <= vwap and sma20 < sma50 and near_20sma and rsi2 >= 90.0:
                        short_trigger = True
                        sl = max(highs[i-2:i+1]) + (0.05 * atr)

                # -------------------------------------------------------------
                # 2. MINERVINI VCP + CONNORS RSI(2) SHAKEOUT HYBRID
                # -------------------------------------------------------------
                elif theory_type == "VCP_CONNORS":
                    prev_ranges = [highs[k] - lows[k] for k in range(i-4, i-1)]
                    is_coiled = np.mean(prev_ranges) <= (1.1 * atr)
                    if is_coiled and price >= vwap and rsi2 <= 12.0 and closes[i] > opens[i]:
                        long_trigger = True
                        sl = min(lows[i-3:i+1]) - (0.05 * atr)
                    elif is_coiled and price <= vwap and rsi2 >= 88.0 and closes[i] < opens[i]:
                        short_trigger = True
                        sl = max(highs[i-3:i+1]) + (0.05 * atr)

                # -------------------------------------------------------------
                # 3. SMC LIQUIDITY SWEEP + CONNORS RSI(2) HYBRID
                # -------------------------------------------------------------
                elif theory_type == "SMC_CONNORS":
                    prior_10_low = min(lows[i-10:i])
                    prior_10_high = max(highs[i-10:i])
                    swept_low = (lows[i] < prior_10_low) and (closes[i] > prior_10_low)
                    swept_high = (highs[i] > prior_10_high) and (closes[i] < prior_10_high)

                    if swept_low and rsi2 <= 15.0 and closes[i] > opens[i]:
                        long_trigger = True
                        sl = lows[i] - (0.05 * atr)
                    elif swept_high and rsi2 >= 85.0 and closes[i] < opens[i]:
                        short_trigger = True
                        sl = highs[i] + (0.05 * atr)

                # -------------------------------------------------------------
                # 4. TRIPLE CONFLUENCE MASTER SNIPER (Velez + Connors + VCP + SPY)
                # -------------------------------------------------------------
                elif theory_type == "MASTER_SNIPER":
                    near_20sma = abs(price - sma20) <= (1.2 * atr)
                    prev_ranges = [highs[k] - lows[k] for k in range(i-3, i)]
                    is_coiled = np.mean(prev_ranges) <= (1.2 * atr)

                    if spy_bull and price >= vwap and sma20 > sma50 and near_20sma and is_coiled and rsi2 <= 10.0:
                        long_trigger = True
                        sl = min(lows[i-2:i+1]) - (0.05 * atr)
                    elif spy_bear and price <= vwap and sma20 < sma50 and near_20sma and is_coiled and rsi2 >= 90.0:
                        short_trigger = True
                        sl = max(highs[i-2:i+1]) + (0.05 * atr)

                # -------------------------------------------------------------
                # EXECUTION & TRADE LIFECYCLE
                # -------------------------------------------------------------
                if long_trigger and (price - sl) > 0:
                    risk = price - sl
                    current_sl = sl
                    tp = price + (target_rr * risk)
                    be_lvl = price + (move_be_at * risk)
                    res_r = None

                    for f in idxs[k_pos+1:]:
                        if highs[f] >= tp:
                            res_r = target_rr
                            break
                        if lows[f] <= current_sl:
                            res_r = (current_sl - price) / risk
                            break
                        if highs[f] >= be_lvl and current_sl < price:
                            current_sl = price

                    if res_r is None:
                        res_r = (closes[idxs[-1]] - price) / risk

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r > 0:
                        wins += 1
                    else:
                        losses += 1

                elif short_trigger and (sl - price) > 0:
                    risk = sl - price
                    current_sl = sl
                    tp = price - (target_rr * risk)
                    be_lvl = price - (move_be_at * risk)
                    res_r = None

                    for f in idxs[k_pos+1:]:
                        if lows[f] <= tp:
                            res_r = target_rr
                            break
                        if highs[f] >= current_sl:
                            res_r = (price - current_sl) / risk
                            break
                        if lows[f] <= be_lvl and current_sl > price:
                            current_sl = price

                    if res_r is None:
                        res_r = (price - closes[idxs[-1]]) / risk

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r > 0:
                        wins += 1
                    else:
                        losses += 1

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    ev = total_pnl_r / total_trades if total_trades > 0 else 0.0
    print(f"📊 [{name:<34}] Trades: {total_trades:>3} | Wins: {wins:>2} | Losses: {losses:>2} | Win Rate: {wr:>5.1f}% | Net EV: {ev:+.3f} R")

print("\n" + "=" * 95)
run_hybrid_theory("1. Velez 20 SMA + Connors RSI(2)", "VELEZ_CONNORS")
run_hybrid_theory("2. Minervini VCP + Connors RSI(2)", "VCP_CONNORS")
run_hybrid_theory("3. SMC Sweep + Connors RSI(2)", "SMC_CONNORS")
run_hybrid_theory("4. Triple Confluence Master Sniper", "MASTER_SNIPER")
print("=" * 95)
