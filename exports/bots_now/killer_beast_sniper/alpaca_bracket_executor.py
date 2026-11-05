import os
import json
import requests

APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "REDACTED_BROKER_KEY").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "EnXL5FZSVSQzHvVTGLh1venMboWHXhGeQ2Kgy2gjzX5e").strip()
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

PROFIT_TARGET_PCT = 0.012  # Default fallback +1.2% TP
STOP_LOSS_PCT = 0.008      # Default fallback -0.8% SL
MAX_SPREAD_PCT = 0.0040    # Max 0.40% spread cap

def get_latest_quote(symbol):
    data_url = f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest"
    try:
        r = requests.get(data_url, headers=HEADERS, timeout=5)
        if r.status_code == 200:
            quote = r.json().get("quote", {})
            bid = float(quote.get("bp", 0))
            ask = float(quote.get("ap", 0))
            if bid > 0 and ask > 0:
                return bid, ask, (ask - bid) / ask
    except Exception as e:
        print(f"⚠️ Quote error [{symbol}]: {e}")
    return None, None, None

def send_bracket_order(symbol, side="buy", qty=5, current_price=0.0, tp_price=None, sl_price=None):
    bid, ask, spread_pct = get_latest_quote(symbol)
    if spread_pct and spread_pct > MAX_SPREAD_PCT:
        print(f"⛔ [SPREAD REJECT] {symbol} | Spread: {spread_pct*100:.3f}% > {MAX_SPREAD_PCT*100:.2f}% max")
        return False

    price = current_price if current_price > 0 else (ask if side == "buy" else bid)
    if not price or price <= 0:
        print(f"⚠️ Pricing unavailable for {symbol}")
        return False

    # Calculate default TP/SL if explicit ATR targets were not passed
    if tp_price is None or sl_price is None:
        if side == "buy":
            tp_price = round(price * (1 + PROFIT_TARGET_PCT), 2)
            sl_price = round(price * (1 - STOP_LOSS_PCT), 2)
        else:
            tp_price = round(price * (1 - PROFIT_TARGET_PCT), 2)
            sl_price = round(price * (1 + STOP_LOSS_PCT), 2)

    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": str(round(tp_price, 2))},
        "stop_loss": {"stop_price": str(round(sl_price, 2))}
    }

    try:
        r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=payload, timeout=5)
        if r.status_code in [200, 201]:
            print(f"⚡ [BRACKET FIRED] {symbol} ({side.upper()} {qty}) | Entry: ~${price:.2f} | TP: ${tp_price:.2f} | SL: ${sl_price:.2f}")
            return True
        else:
            print(f"❌ Order Rejected [{symbol}]: {r.text}")
    except Exception as e:
        print(f"⚠️ API Exception [{symbol}]: {e}")
    return False

if __name__ == "__main__":
    print("🚀 Bracket Order Module Ready.")
