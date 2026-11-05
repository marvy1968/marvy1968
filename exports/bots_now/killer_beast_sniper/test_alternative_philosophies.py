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

symbols = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]
print("=" * 90)
print("🏆 BENCHMARK AUDIT: 4 ALTERNATIVE SCALPING PHILOSOPHIES")
print("=" * 90)

all_bars = {}
for s in symbols:
    b = fetch_bars(s, "5Min", 1000)
    if len(b) >= 55:
        all_bars[s] = b
    print(f"📦 Loaded {s:<5} : {len(b)} bars")

def run_philosophy_test(name, strategy_type):
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
            if len(idxs) < 25:
                continue

            day_vol = 0.0
            day_vwap_num = 0.0

            # Pre-calculate ORB 15-min range (First 3 bars: 9:30 - 9:45)
            orb_high = max([highs[k] for k in idxs[:3]])
            orb_low = min([lows[k] for k in idxs[:3]])
            orb_traded = False

            for k_pos, i in enumerate(idxs):
                typical = (highs[i] + lows[i] + closes[i]) / 3.0
                day_vol += vols[i]
                day_vwap_num += typical * vols[i]
                vwap = day_vwap_num / (day_vol + 1e-5)

                if k_pos < 3 or k_pos > len(idxs) - 4 or i < 30:
                    continue

                price = closes[i]
                sma20 = np.mean(closes[i-20:i])
                sma50 = np.mean(closes[i-50:i])
                atr = calculate_atr(bars[max(0, i-20):i+1], period=14)
                avg_vol = np.mean(vols[max(0, i-20):i])
                rvol = vols[i] / (avg_vol + 1e-5)
                rsi2 = calculate_rsi(closes[max(0, i-10):i+1], period=2)

                long_trigger = False
                short_trigger = False
                sl, tp, be_lvl = 0, 0, 0
                target_rr = 0.75

                # -------------------------------------------------------------
                # 1. CRABEL OPENING RANGE BREAKOUT (ORB-15)
                # -------------------------------------------------------------
                if strategy_type == "ORB_15":
                    if not orb_traded and k_pos in [3, 4, 5, 6]:  # Triggered 9:45 - 10:05
                        if closes[i] > orb_high and rvol >= 1.25:
                            long_trigger = True
                            orb_traded = True
                            sl = orb_low
                            risk = price - sl
                            tp = price + (0.75 * risk)
                            be_lvl = price + (0.40 * risk)
                        elif closes[i] < orb_low and rvol >= 1.25:
                            short_trigger = True
                            orb_traded = True
                            sl = orb_high
                            risk = sl - price
                            tp = price - (0.75 * risk)
                            be_lvl = price - (0.40 * risk)

                # -------------------------------------------------------------
                # 2. LARRY CONNORS RSI(2) TREND PULLBACK
                # -------------------------------------------------------------
                elif strategy_type == "CONNORS_RSI2":
                    # Uptrend + Oversold pullback
                    if (price > vwap) and (sma20 > sma50) and (rsi2 <= 10.0):
                        long_trigger = True
                        sl = min(lows[i-2:i+1]) - (0.05 * atr)
                        risk = price - sl
                        tp = price + (0.75 * risk)
                        be_lvl = price + (0.40 * risk)
                    # Downtrend + Overbought pullback
                    elif (price < vwap) and (sma20 < sma50) and (rsi2 >= 90.0):
                        short_trigger = True
                        sl = max(highs[i-2:i+1]) + (0.05 * atr)
                        risk = sl - price
                        tp = price - (0.75 * risk)
                        be_lvl = price - (0.40 * risk)

                # -------------------------------------------------------------
                # 3. VWAP 2.0-ATR EXHAUSTION FADE (MEAN REVERSION)
                # -------------------------------------------------------------
                elif strategy_type == "VWAP_FADE":
                    dist_from_vwap = (price - vwap) / atr
                    # Extended below VWAP by >= 1.8 ATR with green reversal candle
                    if dist_from_vwap <= -1.8 and closes[i] > opens[i]:
                        long_trigger = True
                        sl = lows[i] - (0.1 * atr)
                        risk = price - sl
                        tp = price + (1.0 * risk)  # Scalp back to VWAP
                        be_lvl = price + (0.50 * risk)
                    # Extended above VWAP by >= 1.8 ATR with red reversal candle
                    elif dist_from_vwap >= 1.8 and closes[i] < opens[i]:
                        short_trigger = True
                        sl = highs[i] + (0.1 * atr)
                        risk = sl - price
                        tp = price - (1.0 * risk)
                        be_lvl = price - (0.50 * risk)

                # -------------------------------------------------------------
                # 4. SMART MONEY / TURTLE SOUP LIQUIDITY SWEEPS
                # -------------------------------------------------------------
                elif strategy_type == "LIQUIDITY_SWEEP":
                    prior_10_low = min(lows[i-10:i])
                    prior_10_high = max(highs[i-10:i])
                    
                    # Low swept but closed back inside range with lower tail
                    swept_low = (lows[i] < prior_10_low) and (closes[i] > prior_10_low)
                    swept_high = (highs[i] > prior_10_high) and (closes[i] < prior_10_high)

                    if swept_low and closes[i] > opens[i]:
                        long_trigger = True
                        sl = lows[i] - (0.05 * atr)
                        risk = price - sl
                        tp = price + (0.85 * risk)
                        be_lvl = price + (0.45 * risk)
                    elif swept_high and closes[i] < opens[i]:
                        short_trigger = True
                        sl = highs[i] + (0.05 * atr)
                        risk = sl - price
                        tp = price - (0.85 * risk)
                        be_lvl = price - (0.45 * risk)

                # Execution & Trade Lifecycle
                if long_trigger and (price - sl) > 0:
                    current_sl = sl
                    res_r = None
                    for f in idxs[k_pos+1:]:
                        if highs[f] >= tp:
                            res_r = (tp - price) / (price - sl)
                            break
                        if lows[f] <= current_sl:
                            res_r = (current_sl - price) / (price - sl)
                            break
                        if highs[f] >= be_lvl and current_sl < price:
                            current_sl = price
                    if res_r is None:
                        res_r = (closes[idxs[-1]] - price) / (price - sl)

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r > 0:
                        wins += 1
                    else:
                        losses += 1

                elif short_trigger and (sl - price) > 0:
                    current_sl = sl
                    res_r = None
                    for f in idxs[k_pos+1:]:
                        if lows[f] <= tp:
                            res_r = (price - tp) / (sl - price)
                            break
                        if highs[f] >= current_sl:
                            res_r = (price - current_sl) / (sl - price)
                            break
                        if lows[f] <= be_lvl and current_sl > price:
                            current_sl = price
                    if res_r is None:
                        res_r = (price - closes[idxs[-1]]) / (sl - price)

                    total_trades += 1
                    total_pnl_r += res_r
                    if res_r > 0:
                        wins += 1
                    else:
                        losses += 1

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    ev = total_pnl_r / total_trades if total_trades > 0 else 0.0
    print(f"📊 [{name:<32}] Trades: {total_trades:>3} | Wins: {wins:>2} | Losses: {losses:>3} | Win Rate: {wr:>4.1f}% | Net EV: {ev:+.3f} R")

print("\n" + "=" * 90)
run_philosophy_test("1. Crabel Opening Range Breakout (ORB)", "ORB_15")
run_philosophy_test("2. Larry Connors RSI(2) Pullback", "CONNORS_RSI2")
run_philosophy_test("3. VWAP 2.0-ATR Exhaustion Fade", "VWAP_FADE")
run_philosophy_test("4. Smart Money Liquidity Sweep", "LIQUIDITY_SWEEP")
print("=" * 90)
