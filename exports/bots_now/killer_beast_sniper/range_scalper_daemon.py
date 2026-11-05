import os
import time
import requests
import numpy as np

APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

# Range Parameters
PROFIT_TARGET_PCT = 0.010  # +1.0% Mean Reversion TP
STOP_LOSS_PCT = 0.007      # -0.7% Tight SL
MAX_SPREAD_PCT = 0.0040    # Max 0.40% Spread Cap

# Asset Universe Selected for High Liquidity / Range Bound Action
RANGE_BASKET = ["AAPL", "MSFT", "GOOGL", "AMZN", "JPM", "V", "CSCO", "PEP", "KO", "XOM"]

def get_bars(symbol, timeframe="5Min", limit=50):
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 200:
            return r.json().get("bars", [])
    except Exception as e:
        print(f"⚠️ Bar Error [{symbol}]: {e}")
    return []

def calculate_rsi(prices, period=14):
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def evaluate_range_conditions(symbol):
    bars = get_bars(symbol, "5Min", 50)
    if len(bars) < 30:
        return None

    closes = np.array([b["c"] for b in bars])
    current_price = closes[-1]

    # 1. Bollinger Bands (20, 2.0)
    ma20 = np.mean(closes[-20:])
    std20 = np.std(closes[-20:])
    upper_band = ma20 + (2.0 * std20)
    lower_band = ma20 - (2.0 * std20)
    bandwidth = (upper_band - lower_band) / ma20

    # 2. RSI (14)
    rsi = calculate_rsi(closes, 14)

    print(f"🔄 [{symbol:<5}] Price: ${current_price:.2f} | MA20: ${ma20:.2f} | Lower: ${lower_band:.2f} | Upper: ${upper_band:.2f} | RSI: {rsi:.1f}")

    # LONG RANGE TRIGGER (Price at/below Lower Band + Oversold RSI)
    if current_price <= lower_band and rsi <= 38:
        return "BUY_LONG"

    # SHORT RANGE TRIGGER (Price at/above Upper Band + Overbought RSI)
    elif current_price >= upper_band and rsi >= 62:
        return "SELL_SHORT"

    return "HOLD"

def run_range_sweep(qty=5):
    print("=" * 70)
    print("🎯 DYNAMIC RANGE BOT — 5-MINUTE MEAN REVERSION SWEEP")
    print("=" * 70)

    from alpaca_bracket_executor import send_bracket_order

    for symbol in RANGE_BASKET:
        signal = evaluate_range_conditions(symbol)
        if signal == "BUY_LONG":
            print(f"🟢 [RANGE BUY TRIGGERED] {symbol} at Lower Band Support!")
            send_bracket_order(symbol=symbol, side="buy", qty=qty)
        elif signal == "SELL_SHORT":
            print(f"🔴 [RANGE SHORT TRIGGERED] {symbol} at Upper Band Resistance!")
            send_bracket_order(symbol=symbol, side="sell", qty=qty)

    print("=" * 70)

if __name__ == "__main__":
    run_range_sweep(qty=5)
