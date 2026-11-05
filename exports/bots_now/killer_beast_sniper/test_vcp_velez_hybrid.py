import os
import re
import requests
import numpy as np

SHADOW_LOG = "/home/moonrider1968/bots/killer_beast_sniper/logs/multi_scalper_shadow.log"
APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "REDACTED_BROKER_KEY").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "EnXL5FZSVSQzHvVTGLh1venMboWHXhGeQ2Kgy2gjzX5e").strip()
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

def fetch_bars(symbol, timeframe="5Min", limit=1000):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bars = data.get("bars")
            return bars if bars is not None else []
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

    atr_smooth = sum(tr_list[:period])
    plus_di_smooth = sum(plus_dm_list[:period])
    minus_di_smooth = sum(minus_dm_list[:period])

    dx_list = []

    for i in range(period, len(tr_list)):
        atr_smooth = atr_smooth - (atr_smooth / period) + tr_list[i]
        plus_di_smooth = plus_di_smooth - (plus_di_smooth / period) + plus_dm_list[i]
        minus_di_smooth = minus_di_smooth - (minus_di_smooth / period) + minus_dm_list[i]

        plus_di = 100 * (plus_di_smooth / atr_smooth) if atr_smooth != 0 else 0
        minus_di = 100 * (minus_di_smooth / atr_smooth) if atr_smooth != 0 else 0

        di_sum = plus_di + minus_di
        dx = 100 * (abs(plus_di - minus_di) / di_sum) if di_sum != 0 else 0
        dx_list.append(dx)

    if len(dx_list) < period:
        return 0.0

    return sum(dx_list[-period:]) / period

def parse_shadow_log():
    if not os.path.exists(SHADOW_LOG):
        return ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
    symbols = []
    try:
        with open(SHADOW_LOG, "r") as f:
            for line in f:
                matches = re.findall(r'\[([A-Z]{1,5})\]', line)
                for m in matches:
                    if m not in ["INFO", "ERROR", "WARN", "STOCKS", "SPY"]:
                        symbols.append(m)
    except Exception:
        pass
    unique = list(set(symbols))
    return unique if unique else ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]

def run_hybrid_backtest():
    symbols = parse_shadow_log()
    print("=" * 80)
    print("⚡ HYBRID SYSTEM AUDIT: MINERVINI VCP SQUEEZE + OLIVER VELEZ 20 SMA PULLBACK")
    print("=" * 80)

    wins = 0
    losses = 0

    for sym in symbols:
        bars = fetch_bars(sym, "5Min", 1000)
        if not bars or len(bars) < 55:
            continue

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

            # 1. Minervini VCP Compression Check (Preceding 3-bar range tightening)
            prev_3_ranges = [highs[k] - lows[k] for k in range(i-3, i)]
            avg_contraction_range = np.mean(prev_3_ranges)
            is_vcp_compressed = avg_contraction_range <= (0.85 * atr)

            # 2. Velez Location & Trend Confluence
            near_20sma = abs(price - sma20) <= (1.2 * atr)
            strong_trend = adx >= 20.0
            
            # 3. Velez Trigger Candles
            bar_range = highs[i] - lows[i]
            body_size = abs(closes[i] - opens[i])
            avg_vol = np.mean(vols[i-20:i-1])
            rvol = vols[i] / (avg_vol + 1e-5)
            volume_confirmed = rvol >= 1.05

            ma_fan_long = (sma20 > sma50) and (price > sma50)
            is_elephant = (body_size >= 0.75 * atr) and (closes[i] > opens[i])
            lower_tail = min(opens[i], closes[i]) - lows[i]
            is_bottoming_tail = (lower_tail >= 0.4 * bar_range) and (closes[i] > opens[i])

            ma_fan_short = (sma20 < sma50) and (price < sma50)
            is_red_elephant = (body_size >= 0.75 * atr) and (closes[i] < opens[i])
            upper_tail = highs[i] - max(opens[i], closes[i])
            is_topping_tail = (upper_tail >= 0.4 * bar_range) and (closes[i] < opens[i])

            # 🟢 HYBRID LONG: VCP Contraction -> 20 SMA Location -> Velez Elephant/Tail Ignition
            if ma_fan_long and is_vcp_compressed and price >= vwap and sma20_slope > 0.0001 and near_20sma and strong_trend and volume_confirmed and (is_elephant or is_bottoming_tail):
                sl = min(lows[i-3:i+1]) - (0.1 * atr)  # Protected below the entire VCP base
                risk = price - sl
                if risk <= 0:
                    continue
                tp = price + (1.5 * risk)

                for f in range(i + 1, i + 13):
                    if lows[f] <= sl:
                        losses += 1
                        break
                    elif highs[f] >= tp:
                        wins += 1
                        break

            # 🔴 HYBRID SHORT: VCP Contraction -> 20 SMA Location -> Velez Bear Elephant/Tail Ignition
            elif ma_fan_short and is_vcp_compressed and price <= vwap and sma20_slope < -0.0001 and near_20sma and strong_trend and volume_confirmed and (is_red_elephant or is_topping_tail):
                sl = max(highs[i-3:i+1]) + (0.1 * atr)  # Protected above the entire VCP base
                risk = sl - price
                if risk <= 0:
                    continue
                tp = price + (1.5 * risk)

                for f in range(i + 1, i + 13):
                    if highs[f] >= sl:
                        losses += 1
                        break
                    elif lows[f] <= tp:
                        wins += 1
                        break

    tot = wins + losses
    wr = (wins / tot * 100) if tot > 0 else 0.0

    print("\n" + "=" * 80)
    print("📊 SHADOW AUDIT — MINERVINI VCP + OLIVER VELEZ HYBRID RESULTS")
    print("=" * 80)
    print(f"🎯 Total High-Confluence Hybrid Setups : {tot}")
    print(f"🟢 Wins                                : {wins}")
    print(f"🔴 Losses                              : {losses}")
    print(f"📈 Win Rate                            : {wr:.1f}%")
    print(f"⚖️ Risk-Reward Ratio                  : 1.50 : 1")
    print("=" * 80)

if __name__ == "__main__":
    run_hybrid_backtest()
