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

def fetch_historical_bars(symbol, timeframe="15Min", limit=1000):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 200:
            return r.json().get("bars", [])
    except Exception as e:
        pass
    return []

def calculate_bollinger_bands(closes, period=20, std_dev=2.0):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]
    ma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return ma + (std_dev * std), ma, ma - (std_dev * std)

def calculate_fibonacci_levels(highs, lows):
    r_high = max(highs[-20:])
    r_low = min(lows[-20:])
    diff = r_high - r_low if r_high != r_low else 1.0
    return {
        "0.0": r_low,
        "0.500": r_low + (0.500 * diff),
        "1.000": r_high,
        "1.272_ext": r_high + (0.272 * diff),
        "-0.272_ext": r_low - (0.272 * diff)
    }

def evaluate_weinstein_stage(bars):
    if len(bars) < 25:
        return "STAGE_1_FLAT"
    closes = [b["c"] for b in bars]
    ma20 = np.mean(closes[-20:])
    ma20_prev = np.mean(closes[-25:-5])
    ma_slope = (ma20 - ma20_prev) / ma20_prev
    if ma_slope > 0.0002:
        return "STAGE_2_UPTREND"
    elif ma_slope < -0.0002:
        return "STAGE_4_DOWNTREND"
    return "RANGE_BOUND"

def parse_shadow_log():
    if not os.path.exists(SHADOW_LOG):
        print(f"⚠️ Shadow log not found at: {SHADOW_LOG}")
        return []
    
    symbols = []
    try:
        with open(SHADOW_LOG, "r") as f:
            for line in f:
                matches = re.findall(r'\[([A-Z]{1,5})\]', line)
                for m in matches:
                    if m not in ["INFO", "ERROR", "WARN", "STOCKS", "SPY"]:
                        symbols.append(m)
    except Exception as e:
        print(f"⚠️ Error reading shadow log: {e}")
    
    unique_symbols = list(set(symbols))
    return unique_symbols if unique_symbols else ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]

def run_shadow_mtf_backtest():
    symbols = parse_shadow_log()
    print("=" * 75)
    print("🔬 SHADOW BACKTEST — MULTI-TIMEFRAME (15M / 1H / 24H) + FIB & BOLLINGER")
    print("=" * 75)
    print(f"📂 Evaluating Tickers ({len(symbols)}): {', '.join(symbols[:10])}...")

    wins = 0
    losses = 0

    for sym in symbols:
        bars_15m = fetch_historical_bars(sym, "15Min", 1000)
        bars_1h = fetch_historical_bars(sym, "1Hour", 500)
        bars_1d = fetch_historical_bars(sym, "1Day", 100)

        if len(bars_15m) < 50 or len(bars_1h) < 25 or len(bars_1d) < 25:
            continue

        stage_1d = evaluate_weinstein_stage(bars_1d)
        stage_1h = evaluate_weinstein_stage(bars_1h)

        closes = [b["c"] for b in bars_15m]
        highs = [b["h"] for b in bars_15m]
        lows = [b["l"] for b in bars_15m]

        for i in range(35, len(bars_15m) - 12):
            price = closes[i]
            ma20 = np.mean(closes[i-20:i])
            ma20_prev = np.mean(closes[i-25:i-5])
            ma_slope = (ma20 - ma20_prev) / ma20_prev

            bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(closes[i-20:i])
            fibs = calculate_fibonacci_levels(highs[i-20:i], lows[i-20:i])

            # 🟢 LONG TRIGGER: 1D & 1H Stage 2 + 15m Price <= 50% Fib + Price <= Lower BB
            if stage_1d == "STAGE_2_UPTREND" and stage_1h == "STAGE_2_UPTREND":
                if price <= fibs["0.500"] and price <= (bb_lower * 1.002):
                    tp_target = min(bb_upper, fibs["1.272_ext"])
                    sl_target = fibs["0.0"] * 0.995

                    for f in range(i + 1, i + 13):
                        if lows[f] <= sl_target:
                            losses += 1
                            break
                        elif highs[f] >= tp_target:
                            wins += 1
                            break

            # 🔴 SHORT TRIGGER: 1D & 1H Stage 4 + 15m Price >= 50% Fib + Price >= Upper BB
            elif stage_1d == "STAGE_4_DOWNTREND" and stage_1h == "STAGE_4_DOWNTREND":
                if price >= fibs["0.500"] and price >= (bb_upper * 0.998):
                    tp_target = max(bb_lower, fibs["-0.272_ext"])
                    sl_target = fibs["1.000"] * 1.005

                    for f in range(i + 1, i + 13):
                        if highs[f] >= sl_target:
                            losses += 1
                            break
                        elif lows[f] <= tp_target:
                            wins += 1
                            break

    tot_trades = wins + losses
    tot_wr = (wins / tot_trades * 100) if tot_trades > 0 else 0.0

    print("\n" + "=" * 75)
    print("📊 SHADOW MULTI-TIMEFRAME FIB/BB AUDIT REPORT")
    print("=" * 75)
    print(f"🎯 Total MTF Signals Evaluated     : {tot_trades}")
    print(f"🟢 Wins                             : {wins}")
    print(f"🔴 Losses                           : {losses}")
    print(f"📈 Win Rate                         : {tot_wr:.1f}%")
    print("=" * 75)

if __name__ == "__main__":
    run_shadow_mtf_backtest()
