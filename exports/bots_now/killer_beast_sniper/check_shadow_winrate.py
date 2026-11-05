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

def fetch_historical_bars(symbol, timeframe="5Min", limit=1000):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 200:
            return r.json().get("bars", [])
    except Exception as e:
        pass
    return []

def calculate_atr(bars, period=14):
    if len(bars) < period + 1:
        return bars[-1]["h"] - bars[-1]["l"]
    tr_list = []
    for j in range(1, len(bars)):
        h = bars[j]["h"]
        l = bars[j]["l"]
        cp = bars[j-1]["c"]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        tr_list.append(tr)
    return np.mean(tr_list[-period:])

def parse_shadow_log():
    if not os.path.exists(SHADOW_LOG):
        print(f"⚠️ Shadow log file not found at: {SHADOW_LOG}")
        return []
    
    logged_symbols = []
    print(f"📂 Reading shadow log telemetry from: {SHADOW_LOG}")
    try:
        with open(SHADOW_LOG, "r") as f:
            for line in f:
                # Find ticker occurrences logged in shadow telemetry
                matches = re.findall(r'\[([A-Z]{1,5})\]', line)
                for m in matches:
                    if m not in ["INFO", "ERROR", "WARN", "STOCKS", "SPY"]:
                        logged_symbols.append(m)
    except Exception as e:
        print(f"⚠️ Log reading error: {e}")
    
    unique_symbols = list(set(logged_symbols))
    return unique_symbols if unique_symbols else ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]

def evaluate_shadow_winrate():
    symbols = parse_shadow_log()
    print(f"🔍 Evaluated Symbols ({len(symbols)}): {', '.join(symbols[:10])}...")
    
    spy_bars = fetch_historical_bars("SPY", "5Min", 1000)
    if not spy_bars:
        print("⚠️ Could not load SPY benchmark bars.")
        return

    tp_mult = 1.5
    sl_mult = 0.8
    forward_bars = 12

    total_wins = 0
    total_losses = 0

    for sym in symbols:
        bars = fetch_historical_bars(sym, "5Min", 1000)
        if len(bars) < 35:
            continue

        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        spy_closes = [b["c"] for b in spy_bars]

        min_len = min(len(bars), len(spy_bars))

        for i in range(25, min_len - forward_bars):
            ma20 = np.mean(closes[i-20:i])
            ma20_prev = np.mean(closes[i-25:i-5])
            ma_slope = (ma20 - ma20_prev) / ma20_prev
            price = closes[i]

            spy_ma20 = np.mean(spy_closes[i-20:i])
            spy_ma20_prev = np.mean(spy_closes[i-25:i-5])
            spy_slope = (spy_ma20 - spy_ma20_prev) / spy_ma20_prev

            r_high = max(highs[i-20:i])
            r_low = min(lows[i-20:i])
            r_span = r_high - r_low if r_high != r_low else 1
            r_pos = (price - r_low) / r_span

            atr = calculate_atr(bars[i-20:i+1], period=14)
            if atr <= 0:
                continue

            tp_dist = atr * tp_mult
            sl_dist = atr * sl_mult

            # Long Condition: Stage 2 + SPY Bullish + Dip <= 35% + Green Candle
            if ma_slope > 0.0002 and spy_slope > 0.0 and r_pos <= 0.35 and closes[i] > opens[i]:
                for f in range(i + 1, i + forward_bars + 1):
                    if lows[f] <= (price - sl_dist):
                        total_losses += 1
                        break
                    elif highs[f] >= (price + tp_dist):
                        total_wins += 1
                        break

            # Short Condition: Stage 4 + SPY Bearish + Bounce >= 65% + Red Candle
            elif ma_slope < -0.0002 and spy_slope < 0.0 and r_pos >= 0.65 and closes[i] < opens[i]:
                for f in range(i + 1, i + forward_bars + 1):
                    if highs[f] >= (price + sl_dist):
                        total_losses += 1
                        break
                    elif lows[f] <= (price - tp_dist):
                        total_wins += 1
                        break

    tot_trades = total_wins + total_losses
    tot_wr = (total_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    expectancy = (tot_wr/100 * tp_mult) - ((1 - tot_wr/100) * sl_mult)

    print("\n" + "=" * 70)
    print("📊 SHADOW LOG WIN RATE AUDIT REPORT")
    print("=" * 70)
    print(f"🎯 Total Simulated Signals Evaluated : {tot_trades}")
    print(f"🟢 Wins                             : {total_wins}")
    print(f"🔴 Losses                           : {total_losses}")
    print(f"📈 Win Rate                         : {tot_wr:.1f}%")
    print(f"⚖️ Risk-to-Reward Ratio             : {tp_mult}x TP / {sl_mult}x SL ({tp_mult/sl_mult:.2f}:1)")
    print(f"💰 Net Expectancy Per Trade         : {expectancy:+.3f}x ATR")
    print("=" * 70)

if __name__ == "__main__":
    evaluate_shadow_winrate()
