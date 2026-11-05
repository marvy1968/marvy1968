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

def fetch_bars(symbol, timeframe="1Hour", limit=1000):
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

def test_minervini_tf(tf_string, tf_label):
    symbols = parse_shadow_log()
    wins = 0
    losses = 0

    for sym in symbols:
        bars = fetch_bars(sym, tf_string, 1000)
        if not bars or len(bars) < 120:
            continue

        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        vols = [b["v"] for b in bars]

        # Stage 2 Trend Template check with calibrated lookbacks
        for i in range(100, len(bars) - 15):
            price = closes[i]
            sma100 = np.mean(closes[i-100:i])
            sma50 = np.mean(closes[i-50:i])
            sma20 = np.mean(closes[i-20:i])
            atr = calculate_atr(bars[i-20:i+1], period=14)

            # 1. Minervini Trend Template (Price > 50 SMA > 100 SMA)
            trend_long = (price > sma50) and (sma50 > sma100) and (sma20 > sma50)
            if not trend_long:
                continue

            # 2. VCP Contraction Check
            t1_range = max(highs[i-12:i-6]) - min(lows[i-12:i-6])
            t2_range = max(highs[i-5:i]) - min(lows[i-5:i])

            # Contraction wave check
            is_contracted = (t2_range < 0.85 * t1_range) and (t2_range <= 2.0 * atr)

            # 3. Volume Expansion Breakout
            avg_vol = np.mean(vols[i-20:i-1])
            rvol = vols[i] / (avg_vol + 1e-5)
            breakout_volume = rvol >= 1.20

            pivot_high = max(highs[i-5:i])

            # 🟢 LONG VCP BREAKOUT
            if is_contracted and breakout_volume and (closes[i] > pivot_high):
                pivot_low = min(lows[i-5:i])
                sl = pivot_low - (0.1 * atr)
                risk = price - sl
                if risk <= 0:
                    continue
                tp = price + (1.5 * risk)

                for f in range(i + 1, min(i + 16, len(bars))):
                    if lows[f] <= sl:
                        losses += 1
                        break
                    elif highs[f] >= tp:
                        wins += 1
                        break

    tot = wins + losses
    wr = (wins / tot * 100) if tot > 0 else 0.0
    return tot, wins, losses, wr

if __name__ == "__main__":
    timeframes = [
        ("1Hour", "1-Hour VCP Swing"),
        ("1Day", "Daily (1D) Native SEPA")
    ]
    print("=" * 80)
    print("🏆 MARK MINERVINI VCP / SEPA — HIGHER TIMEFRAME AUDIT")
    print("=" * 80)

    for tf_code, tf_label in timeframes:
        tot, wins, losses, wr = test_minervini_tf(tf_code, tf_label)
        print(f"📊 [{tf_label:<25}] Trades: {tot:>3} | Wins: {wins:>2} | Losses: {losses:>2} | Win Rate: {wr:.1f}%")
    print("=" * 80)
