import os
import requests
import numpy as np

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
        print(f"⚠️ Exception [{symbol}]: {e}")
    return []

def calculate_vwap(bars_slice):
    """Calculates Volume Weighted Average Price."""
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

def simulate_parameters_confluence(bars, spy_bars, range_dip, range_bounce, tp_mult, sl_mult, forward_bars=12):
    if len(bars) < 35 or len(spy_bars) < 35:
        return 0, 0

    opens = [b["o"] for b in bars]
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]

    spy_closes = [b["c"] for b in spy_bars]

    wins = 0
    losses = 0

    min_len = min(len(bars), len(spy_bars))

    for i in range(25, min_len - forward_bars):
        # Stock Moving Average
        ma20 = np.mean(closes[i-20:i])
        ma20_prev = np.mean(closes[i-25:i-5])
        ma_slope = (ma20 - ma20_prev) / ma20_prev
        price = closes[i]

        # SPY Market Trend Slope
        spy_ma20 = np.mean(spy_closes[i-20:i])
        spy_ma20_prev = np.mean(spy_closes[i-25:i-5])
        spy_slope = (spy_ma20 - spy_ma20_prev) / spy_ma20_prev

        # VWAP Confluence Benchmark
        vwap = calculate_vwap(bars[max(0, i-50):i+1])

        # Range Boundaries
        r_high = max(highs[i-20:i])
        r_low = min(lows[i-20:i])
        r_span = r_high - r_low if r_high != r_low else 1
        r_pos = (price - r_low) / r_span

        atr = calculate_atr(bars[i-20:i+1], period=14)
        if atr <= 0:
            continue

        tp_dist = atr * tp_mult
        sl_dist = atr * sl_mult

        # 🟢 CONFLUENCE LONG: Stock Stage 2 + SPY Bullish + Price Above VWAP + Dip <= range_dip + Green Candle
        if ma_slope > 0.0002 and spy_slope > 0.0 and price >= vwap and r_pos <= range_dip and closes[i] > opens[i]:
            for f in range(i + 1, i + forward_bars + 1):
                if lows[f] <= (price - sl_dist):
                    losses += 1
                    break
                elif highs[f] >= (price + tp_dist):
                    wins += 1
                    break

        # 🔴 CONFLUENCE SHORT: Stock Stage 4 + SPY Bearish + Price Below VWAP + Bounce >= range_bounce + Red Candle
        elif ma_slope < -0.0002 and spy_slope < 0.0 and price <= vwap and r_pos >= range_bounce and closes[i] < opens[i]:
            for f in range(i + 1, i + forward_bars + 1):
                if highs[f] >= (price + sl_dist):
                    losses += 1
                    break
                elif lows[f] <= (price - tp_dist):
                    wins += 1
                    break

    return wins, losses

def run_parameter_grid_search():
    symbols = ["NVDA", "AAPL", "MSFT", "AMD", "AMZN", "TSLA", "META", "GOOGL"]

    print("=" * 75)
    print("🔬 CONFLUENCE BACKTEST — ATR SCALP + VWAP + SPY MARKET FILTER")
    print("=" * 75)

    bars_dict = {}
    for sym in symbols:
        bars = fetch_historical_bars(sym, timeframe="5Min", limit=1000)
        bars_dict[sym] = bars

    spy_bars = fetch_historical_bars("SPY", timeframe="5Min", limit=1000)
    print(f"📦 Loaded {len(spy_bars)} historical 5m bars for SPY Benchmark")

    param_grid = [
        {"dip": 0.35, "bounce": 0.65, "tp_m": 1.2, "sl_m": 0.8, "name": "Confluence Micro-Scalp (1.2x ATR TP / 0.8x ATR SL)"},
        {"dip": 0.40, "bounce": 0.60, "tp_m": 1.5, "sl_m": 0.8, "name": "Confluence Extended    (1.5x ATR TP / 0.8x ATR SL)"},
    ]

    for config in param_grid:
        total_w = 0
        total_l = 0
        print(f"\n🧪 Testing Config: {config['name']}")
        
        for sym in symbols:
            bars = bars_dict.get(sym, [])
            w, l = simulate_parameters_confluence(bars, spy_bars, config["dip"], config["bounce"], config["tp_m"], config["sl_m"], forward_bars=12)
            total_w += w
            total_l += l

        tot_trades = total_w + total_l
        tot_wr = (total_w / tot_trades * 100) if tot_trades > 0 else 0.0
        expectancy = (tot_wr/100 * config["tp_m"]) - ((1 - tot_wr/100) * config["sl_m"])
        print(f"📊 SUMMARY -> Trades: {tot_trades} | Wins: {total_w} | Losses: {total_l} | Win Rate: {tot_wr:.1f}% | Expectancy/Trade: {expectancy:+.3f}x ATR")

    print("\n" + "=" * 75)

if __name__ == "__main__":
    run_parameter_grid_search()
