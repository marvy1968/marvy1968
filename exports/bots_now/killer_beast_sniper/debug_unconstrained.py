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
            return data.get("bars", []) or []
        else:
            print(f"⚠️ API Error [{symbol}]: HTTP {r.status_code} - {r.text}")
    except Exception as e:
        print(f"⚠️ Exception [{symbol}]: {e}")
    return []

symbols = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
print("=" * 75)
print("🔍 ALPACA DATA API & RAW TRIGGER DIAGNOSTIC")
print("=" * 75)

wins, losses = 0, 0
total_candles_checked = 0
bull_triggers = 0
bear_triggers = 0

for sym in symbols:
    bars = fetch_bars(sym, "5Min", 1000)
    print(f"📌 {sym:<5} | Bars Retrieved: {len(bars):>4}")
    if len(bars) < 30:
        continue

    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    opens = [b["o"] for b in bars]

    for i in range(20, len(bars) - 12):
        total_candles_checked += 1
        price = closes[i]
        sma20 = np.mean(closes[i-20:i])
        
        # Simple True Range
        tr_list = [max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1])) for k in range(i-14, i)]
        atr = np.mean(tr_list) if tr_list else 1.0

        bar_range = highs[i] - lows[i]
        body_size = abs(closes[i] - opens[i])

        # Raw Price Location: Close within 1.5x ATR of 20 SMA
        near_20sma = abs(price - sma20) <= (1.5 * atr)

        # Bull Trigger: Green candle with body >= 0.5x ATR or Lower wick >= 35% of candle range
        lower_tail = min(opens[i], closes[i]) - lows[i]
        is_bull = (closes[i] > opens[i]) and (body_size >= 0.5 * atr or (bar_range > 0 and lower_tail / bar_range >= 0.35))

        # Bear Trigger: Red candle with body >= 0.5x ATR or Upper wick >= 35% of candle range
        upper_tail = highs[i] - max(opens[i], closes[i])
        is_bear = (closes[i] < opens[i]) and (body_size >= 0.5 * atr or (bar_range > 0 and upper_tail / bar_range >= 0.35))

        if near_20sma and is_bull:
            bull_triggers += 1
            sl = lows[i] - (0.05 * atr)
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

        elif near_20sma and is_bear:
            bear_triggers += 1
            sl = highs[i] + (0.05 * atr)
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

print("-" * 75)
print(f"📊 Total 5m Candles Scanned : {total_candles_checked}")
print(f"🎯 Bullish Triggers Fired   : {bull_triggers}")
print(f"🎯 Bearish Triggers Fired   : {bear_triggers}")
print(f"🟢 Total Wins               : {wins}")
print(f"🔴 Total Losses             : {losses}")
print(f"📈 Unconstrained Win Rate   : {wr:.1f}% (R:R 1.50 : 1)")
print("=" * 75)
