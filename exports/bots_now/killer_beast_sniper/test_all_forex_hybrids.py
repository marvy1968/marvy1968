import os
import re
import requests
import numpy as np
from datetime import datetime, timezone

# ---------------------------------------------------------
# ⚙️ LOAD .ENV & OANDA CREDENTIALS
# ---------------------------------------------------------
env_path = "/home/moonrider1968/bots/killer_beast_sniper/.env"
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

OANDA_API_KEY = (
    os.getenv("OANDA_API_TOKEN") or 
    os.getenv("OANDA_API_KEY") or 
    os.getenv("OANDA_ACCESS_TOKEN") or ""
).strip()

OANDA_ENV = (os.getenv("OANDA_ENV") or "practice").lower()
OANDA_URL = "https://api-fxtrade.oanda.com/v3" if "live" in OANDA_ENV else "https://api-fxpractice.oanda.com/v3"

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "EUR_JPY", "GBP_JPY"]

def fetch_oanda_bars(instrument, granularity="M5", count=1000):
    url = f"{OANDA_URL}/instruments/{instrument}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            candles = r.json().get("candles", [])
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
    except Exception:
        pass
    return []

def calculate_atr(bars_slice, period=14):
    if len(bars_slice) < period + 1:
        return bars_slice[-1]["h"] - bars_slice[-1]["l"]
    tr_list = []
    for j in range(1, len(bars_slice)):
        h, l, cp = bars_slice[j]["h"], bars_slice[j]["l"], bars_slice[j-1]["c"]
        tr_list.append(max(h - l, abs(h - cp), abs(l - cp)))
    return np.mean(tr_list[-period:]) if tr_list else 0.0010

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

def is_valid_session(timestamp_str):
    try:
        ts_clean = re.sub(r"\.\d+Z", "Z", timestamp_str)
        dt = datetime.fromisoformat(ts_clean.replace("Z", "+00:00"))
        hour_utc = dt.hour + (dt.minute / 60.0)
        # London Open (7:30-11:30 UTC) or London/NY Overlap (13:00-16:30 UTC)
        return (7.5 <= hour_utc <= 11.5) or (13.0 <= hour_utc <= 16.5)
    except Exception:
        return True

print("=" * 100)
print("🌍 MASTER FOREX BENCHMARK AUDIT: 4 FLOW SCALPERS + 4 FLOW HYBRIDS (OANDA 5M)")
print("=" * 100)

all_bars = {}
for p in PAIRS:
    b = fetch_oanda_bars(p, "M5", 1000)
    if len(b) >= 55:
        all_bars[p] = b
    print(f"📦 Loaded {p:<8} : {len(b)} 5m bars")

def run_scalp_benchmark(name, strategy_type, target_rr=0.75, move_be_at=0.40):
    total_trades = 0
    total_pnl_r = 0.0
    wins, losses = 0, 0

    for pair, bars in all_bars.items():
        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        opens = [b["o"] for b in bars]
        timestamps = [b["t"] for b in bars]

        for i in range(50, len(bars) - 15):
            ts = timestamps[i]
            if not is_valid_session(ts):
                continue

            price = closes[i]
            sma20 = np.mean(closes[i-20:i])
            sma50 = np.mean(closes[i-50:i])
            atr = calculate_atr(bars[i-20:i+1], period=14)
            rsi2 = calculate_rsi(closes[i-10:i+1], period=2)

            long_trigger = False
            short_trigger = False
            sl = 0

            # -------------------------------------------------------------
            # PART 1: STANDALONE FLOW SCALPERS
            # -------------------------------------------------------------
            if strategy_type == "LONDON_BOX_EXPANSION":
                asian_high = max(highs[i-24:i-6])
                asian_low = min(lows[i-24:i-6])
                if closes[i] > asian_high and closes[i-1] <= asian_high and (price > sma50):
                    long_trigger = True
                    sl = lows[i] - (0.08 * atr)
                elif closes[i] < asian_low and closes[i-1] >= asian_low and (price < sma50):
                    short_trigger = True
                    sl = highs[i] + (0.08 * atr)

            elif strategy_type == "FVG_IMBALANCE_FILL":
                # Fair Value Gap displacement
                if lows[i-2] > highs[i] and (price > sma50) and closes[i] > opens[i]:
                    long_trigger = True
                    sl = min(lows[i-2:i+1]) - (0.08 * atr)
                elif highs[i-2] < lows[i] and (price < sma50) and closes[i] < opens[i]:
                    short_trigger = True
                    sl = max(highs[i-2:i+1]) + (0.08 * atr)

            elif strategy_type == "BB_KC_SQUEEZE":
                std20 = np.std(closes[i-20:i])
                kc_upper = sma20 + (1.5 * atr)
                kc_lower = sma20 - (1.5 * atr)
                prev_std = np.std(closes[i-25:i-5])
                was_squeezed = (sma20 + 2*prev_std <= kc_upper) and (sma20 - 2*prev_std >= kc_lower)

                if was_squeezed and closes[i] > kc_upper and (price > sma50):
                    long_trigger = True
                    sl = min(lows[i-2:i+1]) - (0.08 * atr)
                elif was_squeezed and closes[i] < kc_lower and (price < sma50):
                    short_trigger = True
                    sl = max(highs[i-2:i+1]) + (0.08 * atr)

            elif strategy_type == "MOMENTUM_PULSE":
                roc5 = (closes[i] - closes[i-5]) / closes[i-5]
                roc10 = (closes[i] - closes[i-10]) / closes[i-10]
                if (price > sma50) and (roc5 > 0.0004) and (roc10 > 0.0008) and (closes[i] > opens[i]):
                    long_trigger = True
                    sl = lows[i] - (0.08 * atr)
                elif (price < sma50) and (roc5 < -0.0004) and (roc10 < -0.0008) and (closes[i] < opens[i]):
                    short_trigger = True
                    sl = highs[i] + (0.08 * atr)

            # -------------------------------------------------------------
            # PART 2: FLOW + REVERSION HYBRID COMBINATIONS
            # -------------------------------------------------------------
            # A. London Box + Connors RSI(2) Shakeout
            elif strategy_type == "HYBRID_BOX_CONNORS":
                asian_high = max(highs[i-24:i-6])
                asian_low = min(lows[i-24:i-6])
                # Pierced Asian Low but reclaimed + RSI(2) oversold panic
                if (lows[i] < asian_low) and (closes[i] > asian_low) and (rsi2 <= 12.0) and (closes[i] > opens[i]):
                    long_trigger = True
                    sl = lows[i] - (0.08 * atr)
                elif (highs[i] > asian_high) and (closes[i] < asian_high) and (rsi2 >= 88.0) and (closes[i] < opens[i]):
                    short_trigger = True
                    sl = highs[i] + (0.08 * atr)

            # B. Squeeze Expansion + Connors RSI(2) Retest
            elif strategy_type == "HYBRID_SQUEEZE_CONNORS":
                std20 = np.std(closes[i-20:i])
                kc_upper = sma20 + (1.5 * atr)
                kc_lower = sma20 - (1.5 * atr)
                prev_std = np.std(closes[i-25:i-5])
                was_squeezed = (sma20 + 2*prev_std <= kc_upper) and (sma20 - 2*prev_std >= kc_lower)
                near_20sma = abs(price - sma20) <= (1.0 * atr)

                if was_squeezed and near_20sma and (rsi2 <= 10.0) and (price > sma50):
                    long_trigger = True
                    sl = min(lows[i-2:i+1]) - (0.08 * atr)
                elif was_squeezed and near_20sma and (rsi2 >= 90.0) and (price < sma50):
                    short_trigger = True
                    sl = max(highs[i-2:i+1]) + (0.08 * atr)

            # C. ICT FVG Imbalance + Connors RSI(2) Mitigation
            elif strategy_type == "HYBRID_FVG_CONNORS":
                fvg_long = (lows[i-2] > highs[i]) and (rsi2 <= 15.0) and (price > sma50)
                fvg_short = (highs[i-2] < lows[i]) and (rsi2 >= 85.0) and (price < sma50)
                if fvg_long and closes[i] > opens[i]:
                    long_trigger = True
                    sl = min(lows[i-2:i+1]) - (0.08 * atr)
                elif fvg_short and closes[i] < opens[i]:
                    short_trigger = True
                    sl = max(highs[i-2:i+1]) + (0.08 * atr)

            # D. Session Liquidity Sweep + FVG Rejection
            elif strategy_type == "HYBRID_SWEEP_FVG":
                prior_15_low = min(lows[i-15:i])
                prior_15_high = max(highs[i-15:i])
                swept_low = (lows[i] < prior_15_low) and (closes[i] > prior_15_low)
                swept_high = (highs[i] > prior_15_high) and (closes[i] < prior_15_high)
                is_fvg = (lows[i-2] > highs[i]) or (highs[i-2] < lows[i])

                if swept_low and is_fvg and closes[i] > opens[i]:
                    long_trigger = True
                    sl = lows[i] - (0.08 * atr)
                elif swept_high and is_fvg and closes[i] < opens[i]:
                    short_trigger = True
                    sl = highs[i] + (0.08 * atr)

            # Execution Engine (+0.75R Target, +0.40R Breakeven)
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
    print(f"📊 [{name:<38}] Trades: {total_trades:>4} | Wins: {wins:>3} | Losses: {losses:>3} | Win Rate: {wr:>5.1f}% | Net EV: {ev:+.3f} R")

print("\n--- PART 1: 4 STANDALONE FOREX FLOW SCALPERS ---")
run_scalp_benchmark("1. London Asian Box Expansion", "LONDON_BOX_EXPANSION")
run_scalp_benchmark("2. ICT Fair Value Gap (FVG) Fill", "FVG_IMBALANCE_FILL")
run_scalp_benchmark("3. Bollinger/Keltner Squeeze", "BB_KC_SQUEEZE")
run_scalp_benchmark("4. Multi-ROC Momentum Pulse", "MOMENTUM_PULSE")

print("\n--- PART 2: 4 FOREX FLOW HYBRID COMBINATIONS ---")
run_scalp_benchmark("A. London Box + Connors Shakeout", "HYBRID_BOX_CONNORS")
run_scalp_benchmark("B. Squeeze Expansion + Connors Dip", "HYBRID_SQUEEZE_CONNORS")
run_scalp_benchmark("C. ICT FVG + Connors Mitigation", "HYBRID_FVG_CONNORS")
run_scalp_benchmark("D. Session Sweep + FVG Rejection", "HYBRID_SWEEP_FVG")
print("=" * 100)
