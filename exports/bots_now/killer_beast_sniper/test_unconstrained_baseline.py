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

def run_unconstrained_audit():
    symbols = parse_shadow_log()
    print("=" * 75)
    print("🔓 UNCONSTRAINED BASELINE AUDIT (RAW 20 SMA + POWER CANDLES ONLY)")
    print("=" * 75)
    print(f"📂 Evaluating Tickers ({len(symbols)}): {', '.join(symbols[:10])}...")

    wins = 0
    losses = 0

    for sym in symbols:
        bars = fetch_bars(sym, "5Min", 1000)
        if not bars or len(bars) < 30:
            continue

        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]

        for i in range(20, len(bars) - 12):
            price = closes[i]
            sma20 = np.mean(closes[i-20:i])
            atr = calculate_atr(bars[i-20:i+1], period=14)

            bar_range = highs[i] - lows[i]
            body_size = abs(closes[i] - opens[i])

            # Raw Location: Anywhere within 1.5 ATR of 20 SMA
            near_20sma = abs(price - sma20) <= (1.5 * atr)

            # Raw Candle Triggers
            is_bull_elephant = (body_size >= 0.6 * atr) and (closes[i] > opens[i])
            lower_tail = min(opens[i], closes[i]) - lows[i]
            is_bottoming_tail = (lower_tail >= 0.3 * bar_range) and (closes[i] > opens[i])

            is_bear_elephant = (body_size >= 0.6 * atr) and (closes[i] < opens[i])
            upper_tail = highs[i] - max(opens[i], closes[i])
            is_topping_tail = (upper_tail >= 0.3 * bar_range) and (closes[i] < opens[i])

            # 🟢 UNCONSTRAINED LONG: Raw Bull Signal Near 20 SMA
            if near_20sma and (is_bull_elephant or is_bottoming_tail):
                sl = lows[i] - (0.1 * atr)
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

            # 🔴 UNCONSTRAINED SHORT: Raw Bear Signal Near 20 SMA
            elif near_20sma and (is_bear_elephant or is_topping_tail):
                sl = highs[i] + (0.1 * atr)
                risk = sl - price
                if risk <= 0:
                    continue
                tp = price - (1.5 * risk)

                for f in range(i + 1, i + 13):
                    if highs[f] >= sl:
                        losses += 1
                        break
                    elif lows[f] <= tp:
                        wins += 1
                        break

    tot = wins + losses
    wr = (wins / tot * 100) if tot > 0 else 0.0

    print("\n" + "=" * 75)
    print("📊 SHADOW AUDIT — UNCONSTRAINED BASELINE RESULTS")
    print("=" * 75)
    print(f"🎯 Total Unconstrained Setups       : {tot}")
    print(f"🟢 Wins                             : {wins}")
    print(f"🔴 Losses                           : {losses}")
    print(f"📈 Win Rate                         : {wr:.1f}%")
    print(f"⚖️ Risk-Reward Ratio               : 1.50 : 1")
    print("=" * 75)

if __name__ == "__main__":
    run_unconstrained_audit()
