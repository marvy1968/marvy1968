import os
import sys
import requests
import yfinance as yf
import pandas as pd

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from master_expert_orchestrator import evaluate_all_experts

# Environment & Alpaca Config (Sanitized with .strip())
APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

TARGETS = [
    ("AAPL", "STOCKS", "1m", "1d", True),
    ("NVDA", "STOCKS", "1m", "1d", True),
    ("TSLA", "STOCKS", "1m", "1d", True),
    ("BTC-USD", "CRYPTO", "1h", "7d", False),
    ("EURUSD=X", "FOREX", "1m", "1d", False)
]

def format_candles(df):
    candles = []
    for idx, row in df.iterrows():
        candles.append({
            'h': float(row['High']),
            'l': float(row['Low']),
            'c': float(row['Close']),
            'close': float(row['Close']),
            'high': float(row['High']),
            'low': float(row['Low'])
        })
    return candles

def get_alpaca_account():
    if not APCA_KEY or not APCA_SECRET:
        return None
    try:
        r = requests.get(f"{BASE_URL}/account", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"⚠️ Account API Returned HTTP {r.status_code}: {r.text}")
    except Exception as e:
        print(f"⚠️ Account API Error: {e}")
    return None

def submit_alpaca_paper_order(symbol, qty, side, order_type="market", time_in_force="gtc"):
    url = f"{BASE_URL}/orders"
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side.lower(),
        "type": order_type,
        "time_in_force": time_in_force
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=5)
        return r.json(), r.status_code
    except Exception as e:
        return str(e), 500

def main():
    print("=" * 85)
    print("📊 DATASET ANALYSIS, EXPERT PARAMETER TUNING & ALPACA PAPER TRADER")
    print("=" * 85)

    account = get_alpaca_account()
    if account:
        equity = float(account.get("equity", 0))
        buying_power = float(account.get("buying_power", 0))
        print(f"💳 ALPACA PAPER ACCOUNT ACTIVE | Equity: ${equity:,.2f} | Buying Power: ${buying_power:,.2f}")
    else:
        print("⚠️ Alpaca credentials missing or unauthorized. Running paper trader in simulation mode.")

    print("-" * 85)

    for ticker, asset_type, interval, period, include_prepost in TARGETS:
        display_ticker = "BTC/USD" if ticker == "BTC-USD" else ("EUR_USD" if ticker == "EURUSD=X" else ticker)
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period=period, interval=interval, prepost=include_prepost)

            if df.empty or len(df) < 30:
                print(f"⚠️ [{display_ticker:<7}] Insufficient market data ({len(df)} bars)")
                print("-" * 85)
                continue

            candles = format_candles(df)
            res = evaluate_all_experts(display_ticker, candles, timeframe=interval)

            if not res:
                continue

            symbol = res['symbol']
            price = res['price']
            regime = res['regime']
            adx = res['adx']
            chop = res['chop']
            signals = res['signals']

            print(f"📌 {symbol:<8} | Type: {asset_type:<6} | Price: ${price:,.2f} | Regime: {regime:<8} (ADX: {adx:.1f}, CHOP: {chop:.1f})")

            if signals:
                for name, side, category in signals:
                    print(f"   ⚡ SIGNAL -> [{category:<7}] Expert: {name:<20} | Side: {side}")

                    # Dispatch Equities to Alpaca Paper Account
                    if asset_type == "STOCKS" and account:
                        qty = max(1, int(5000 / price)) # Allocation ~$5k
                        resp, status = submit_alpaca_paper_order(symbol, qty, side)
                        if status in [200, 201]:
                            print(f"   ✅ [ALPACA ORDER FILLED] {side} {qty} shares of {symbol} (Order ID: {resp.get('id', 'N/A')})")
                        else:
                            print(f"   ❌ [ALPACA ORDER REJECTED] Code {status}: {resp}")
            else:
                print("   ℹ️ No setup threshold matched current candle data.")

            print("-" * 85)

        except Exception as e:
            print(f"❌ [{display_ticker:<7}] Analysis Error: {e}")
            print("-" * 85)

if __name__ == "__main__":
    main()
