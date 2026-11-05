import os
import re
import requests
import numpy as np
from datetime import datetime, timezone

# ---------------------------------------------------------
# ⚙️ LOAD .ENV CREDENTIALS
# ---------------------------------------------------------
env_path = "/home/moonrider1968/bots/killer_beast_sniper/.env"
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

# Support OANDA_API_TOKEN / OANDA_API_KEY
OANDA_API_KEY = (
    os.getenv("OANDA_API_TOKEN") or 
    os.getenv("OANDA_API_KEY") or 
    os.getenv("OANDA_ACCESS_TOKEN") or 
    os.getenv("OANDA_TOKEN") or ""
).strip()

OANDA_ACCOUNT_ID = (os.getenv("OANDA_ACCOUNT_ID") or "").strip()
OANDA_ENV = (os.getenv("OANDA_ENV") or "practice").lower()

if "live" in OANDA_ENV or "trade" in OANDA_ENV:
    OANDA_URL = "https://api-fxtrade.oanda.com/v3"
else:
    OANDA_URL = "https://api-fxpractice.oanda.com/v3"

if not OANDA_API_KEY:
    print("❌ ERROR: OANDA API Token not found in .env!")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "EUR_JPY", "GBP_JPY"]

def fetch_oanda_bars(instrument, granularity="M5", count=1000):
    url = f"{OANDA_URL}/instruments/{instrument}/candles"
    params = {
        "granularity": granularity,
        "count": count,
        "price": "M"
    }
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            candles = data.get("candles", [])
            bars = []
            for c in candles:
                if c.get("complete", False):
                    bars.append({
                        "t": c["time"],
                        "o": float(c["mid"]["o"]),
                        "h": float(c["mid"]["h"]),
                        "l": float(c["mid"]["l"]),
                        "c": float(c["mid"]["c"]),
                        "v": int(c["volume"])
                    })
            return bars
        else:
            print(f"⚠️ OANDA API Error [{instrument}]: HTTP {r.status_code} - {r.text}")
    except Exception as e:
        print(f"⚠️ Exception [{instrument}]: {e}")
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

def is_valid_forex_session(timestamp_str):
    """
    Forex High-Liquidity Windows:
    - London Open: 08:00 - 11:30 UTC (3:00 - 6:30 AM EST)
    - London/NY Overlap: 13:00 - 16:30 UTC (8:00 - 11:30 AM EST)
    """
    try:
        ts_clean = re.sub(r"\.\d+Z", "Z", timestamp_str)
        dt = datetime.fromisoformat(ts_clean.replace("Z", "+00:00"))
        hour_utc = dt.hour + (dt.minute / 60.0)
        london_open = (8.0 <= hour_utc <= 11.5)
        ny_overlap = (13.0 <= hour_utc <= 16.5)
        return london_open or ny_overlap
    except Exception:
        return True

print("=" * 95)
print(f"🌍 OANDA FOREX MULTI-STRATEGY BENCHMARK AUDIT ({OANDA_ENV.upper()})")
print("=" * 95)

all_bars = {}
for p in PAIRS:
    b = fetch_oanda_bars(p, "M5", 1000)
    if len(b) >= 55:
        all_bars[p] = b
    print(f"📦 Loaded {p:<8} : {len(b)} 5m bars")

def run_forex_audit(name, strategy_type, target_rr=0.75, move_be_at=0.40):
    total_trades = 0
    total_pnl_r = 0.0
    wins = 0
    losses = 0

    for pair, bars in all_bars.items():
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        vols = [b["v"] for b in bars]
        timestamps = [b["t"] for b in bars]

        for i in range(50, len(bars) - 15):
            ts = timestamps[i]
            if not is_valid_forex_session(ts):
                continue

            price = closes[i]
            sma20 = np.mean(closes[i-20:i])
            sma50 = np.mean(closes[i-50:i])
            atr = calculate_atr(bars[i-20:i+1], period=14)
            rsi2 = calculate_rsi(closes[i-10:i+1], period=2)

            long_trigger = False
            short_trigger = False
            sl = 0

            # 1. Larry Connors RSI(2) Trend Reversion
            if strategy_type == "CONNORS_RSI2":
                if (price > sma50) and (sma20 > sma50) and (rsi2 <= 10.0):
                    long_trigger = True
                    sl = min(lows[i-2:i+1]) - (0.10 * atr)
                elif (price < sma50) and (sma20 < sma50) and (rsi2 >= 90.0):
                    short_trigger = True
                    sl = max(highs[i-2:i+1]) + (0.10 * atr)

            # 2. Minervini VCP Compression + RSI(2) Shakeout
            elif strategy_type == "VCP_CONNORS":
                prev_ranges = [highs[k] - lows[k] for k in range(i-4, i-1)]
                is_coiled = np.mean(prev_ranges) <= (1.1 * atr)
                if is_coiled and (price > sma50) and (rsi2 <= 12.0) and (closes[i] > opens[i]):
                    long_trigger = True
                    sl = min(lows[i-3:i+1]) - (0.10 * atr)
                elif is_coiled and (price < sma50) and (rsi2 >= 88.0) and (closes[i] < opens[i]):
                    short_trigger = True
                    sl = max(highs[i-3:i+1]) + (0.10 * atr)

            # 3. Velez 20 SMA Retest
            elif strategy_type == "VELEZ_20SMA":
                near_20sma = abs(price - sma20) <= (1.0 * atr)
                body = abs(closes[i] - opens[i])
                if (price > sma50) and (sma20 > sma50) and near_20sma and (body >= 0.6 * atr) and (closes[i] > opens[i]):
                    long_trigger = True
                    sl = lows[i] - (0.10 * atr)
                elif (price < sma50) and (sma20 < sma50) and near_20sma and (body >= 0.6 * atr) and (closes[i] < opens[i]):
                    short_trigger = True
                    sl = highs[i] + (0.10 * atr)

            # 4. Asian Range Liquidity Sweep (London Session)
            elif strategy_type == "SESSION_SWEEP":
                prior_15_low = min(lows[i-15:i])
                prior_15_high = max(highs[i-15:i])
                swept_low = (lows[i] < prior_15_low) and (closes[i] > prior_15_low)
                swept_high = (highs[i] > prior_15_high) and (closes[i] < prior_15_high)

                if swept_low and closes[i] > opens[i]:
                    long_trigger = True
                    sl = lows[i] - (0.10 * atr)
                elif swept_high and closes[i] < opens[i]:
                    short_trigger = True
                    sl = highs[i] + (0.10 * atr)

            # Execution Engine
            if long_trigger and (price - sl) > 0:
                risk = price - sl
                current_sl = sl
                tp = price + (target_rr * risk)
                be_lvl = price + (move_be_at * risk)
                res_r = None

                for f in range(i + 1, min(i + 15, len(bars))):
                    if highs[f] >= tp:
                        res_r = target_rr
                        break
                    if lows[f] <= current_sl:
                        res_r = (current_sl - price) / risk
                        break
                    if highs[f] >= be_lvl and current_sl < price:
                        current_sl = price

                if res_r is None:
                    res_r = (closes[min(i + 14, len(bars) - 1)] - price) / risk

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

                for f in range(i + 1, min(i + 15, len(bars))):
                    if lows[f] <= tp:
                        res_r = target_rr
                        break
                    if highs[f] >= current_sl:
                        res_r = (price - current_sl) / risk
                        break
                    if lows[f] <= be_lvl and current_sl > price:
                        current_sl = price

                if res_r is None:
                    res_r = (price - closes[min(i + 14, len(bars) - 1)]) / risk

                total_trades += 1
                total_pnl_r += res_r
                if res_r > 0:
                    wins += 1
                else:
                    losses += 1

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    ev = total_pnl_r / total_trades if total_trades > 0 else 0.0
    print(f"📊 [{name:<34}] Trades: {total_trades:>4} | Wins: {wins:>3} | Losses: {losses:>3} | Win Rate: {wr:>5.1f}% | Net EV: {ev:+.3f} R")

print("\n" + "=" * 95)
run_forex_audit("1. Larry Connors RSI(2) Trend", "CONNORS_RSI2")
run_forex_audit("2. Minervini VCP + Connors RSI(2)", "VCP_CONNORS")
run_forex_audit("3. Oliver Velez 20 SMA Retest", "VELEZ_20SMA")
run_forex_audit("4. Session Range Liquidity Sweep", "SESSION_SWEEP")
print("=" * 95)
