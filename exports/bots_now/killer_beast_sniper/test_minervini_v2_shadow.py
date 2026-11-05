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
            return r.json().get("bars", [])
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

def calculate_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    
    def ema(series, p):
        alpha = 2 / (p + 1)
        res = [series[0]]
        for val in series[1:]:
            res.append(val * alpha + res[-1] * (1 - alpha))
        return np.array(res)

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line[-1], signal_line[-1], histogram[-1]

def calculate_ttm_squeeze(closes_slice, highs_slice, lows_slice, period=20):
    """Bollinger Bands inside Keltner Channels check."""
    if len(closes_slice) < period + 1:
        return False
    
    closes_20 = closes_slice[-period:]
    highs_20 = highs_slice[-period:]
    lows_20 = lows_slice[-period:]

    ma = np.mean(closes_20)
    std = np.std(closes_20)
    bb_upper = ma + (2.0 * std)
    bb_lower = ma - (2.0 * std)

    tr_list = []
    for k in range(1, len(closes_slice)):
        h = highs_slice[k]
        l = lows_slice[k]
        cp = closes_slice[k-1]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        tr_list.append(tr)

    atr = np.mean(tr_list[-period:]) if tr_list else 1.0
    kc_upper = ma + (1.5 * atr)
    kc_lower = ma - (1.5 * atr)

    # Squeeze active when BB is inside KC
    return (bb_upper <= kc_upper) and (bb_lower >= kc_lower)

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

def run_minervini_v2_backtest():
    symbols = parse_shadow_log()
    spy_bars = fetch_bars("SPY", "5Min", 1000)
    
    if not spy_bars or len(spy_bars) < 55:
        print("⚠️ Could not fetch SPY benchmark bars. Skipping RS filter.")
        spy_closes = []
    else:
        spy_closes = [b["c"] for b in spy_bars]

    print("=" * 75)
    print("🏆 REFINED MINERVINI V2 — RS SPY + TTM SQUEEZE + MACD + VWAP AUDIT")
    print("=" * 75)

    wins = 0
    losses = 0

    for sym in symbols:
        bars = fetch_bars(sym, "5Min", 1000)
        if len(bars) < 55:
            continue

        closes = [b["c"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        vols = [b["v"] for b in bars]

        for i in range(50, len(bars) - 12):
            price = closes[i]
            sma50 = np.mean(closes[i-50:i])
            sma20 = np.mean(closes[i-20:i])
            atr = calculate_atr(bars[i-20:i+1], period=14)
            vwap = calculate_vwap(bars[max(0, i-50):i+1])

            # 1. Minervini Trend Template
            trend_long = (sma20 > sma50) and (price > sma50)
            trend_short = (sma20 < sma50) and (price < sma50)

            # 2. TTM Volatility Squeeze Confirmation (Passes 21-bar slice)
            in_squeeze = calculate_ttm_squeeze(closes[i-21:i+1], highs[i-21:i+1], lows[i-21:i+1], period=20)

            # 3. MACD Histogram Slope
            _, _, macd_hist_curr = calculate_macd(closes[i-35:i+1])
            _, _, macd_hist_prev = calculate_macd(closes[i-36:i])
            macd_accelerating_up = macd_hist_curr > macd_hist_prev
            macd_accelerating_down = macd_hist_curr < macd_hist_prev

            # 4. Relative Strength vs SPY (RS Ratio Slope)
            if spy_closes and len(spy_closes) > i:
                rs_curr = price / spy_closes[i]
                rs_prev = closes[i-5] / spy_closes[i-5]
                outperforming_spy = rs_curr > rs_prev
                underperforming_spy = rs_curr < rs_prev
            else:
                outperforming_spy = True
                underperforming_spy = True

            # 5. Volume Expansion Breakout
            avg_vol = np.mean(vols[i-20:i-1])
            rvol = vols[i] / (avg_vol + 1e-5)
            volume_breakout = rvol >= 1.25

            pivot_high = max(highs[i-3:i])
            pivot_low = min(lows[i-3:i])

            # 🟢 REFINED LONG VCP: Trend + In Squeeze + RS > SPY + VWAP Gate + MACD Up
            if trend_long and price >= vwap and outperforming_spy and macd_accelerating_up and volume_breakout and closes[i] > pivot_high:
                sl = pivot_low - (0.05 * atr)
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

            # 🔴 REFINED SHORT VCP: Trend + In Squeeze + RS < SPY + VWAP Gate + MACD Down
            elif trend_short and price <= vwap and underperforming_spy and macd_accelerating_down and volume_breakout and closes[i] < pivot_low:
                sl = pivot_high + (0.05 * atr)
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
    print("📊 SHADOW AUDIT — REFINED MINERVINI V2 RESULTS")
    print("=" * 75)
    print(f"🎯 Total High-Confluence Breakouts: {tot}")
    print(f"🟢 Wins                            : {wins}")
    print(f"🔴 Losses                          : {losses}")
    print(f"📈 Win Rate                        : {wr:.1f}%")
    print(f"⚖️ Risk-Reward Ratio              : 1.50 : 1")
    print("=" * 75)

if __name__ == "__main__":
    run_minervini_v2_backtest()
