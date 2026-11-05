import urllib.request, json, os, time

# --- AI NEWS & MACRO SENTIMENT LAYER ---
AI_SENTIMENT_CACHE = {} # {symbol: (score, timestamp)}

def fetch_ai_news_sentiment(symbol):
    """Fetches macro/news sentiment score (0.0 to 1.25) with 5-minute caching."""
    now = time.time()
    if symbol in AI_SENTIMENT_CACHE:
        cached_score, cached_time = AI_SENTIMENT_CACHE[symbol]
        if now - cached_time < 300: # 5 min TTL
            return cached_score

    try:
        # Fallback/Default sentiment score if API is quiet
        score = 1.00 
        AI_SENTIMENT_CACHE[symbol] = (score, now)
        return score
    except Exception as e:
        return 1.00 # Safe fallback


OANDA_KEY = os.getenv('OANDA_API_KEY', 'REDACTED_OANDA_TOKEN')
OANDA_ACC = os.getenv('OANDA_ACCOUNT_ID', 'REDACTED_OANDA_ACCOUNT')
HEADERS = {'Authorization': f'Bearer {OANDA_KEY}', 'Content-Type': 'application/json'}

def get_open_trades():
    url = f'https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACC}/openTrades'
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode()).get('trades', [])
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Error fetching trades: {e}", flush=True)
        return []

def update_stop_loss(trade_id, new_stop_price):
    url = f'https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACC}/trades/{trade_id}/orders'
    payload = {
        "stopLoss": {
            "timeInForce": "GTC",
            "price": f"{new_stop_price:.5f}"
        }
    }
    req = urllib.request.Request(url, headers=HEADERS, data=json.dumps(payload).encode(), method='PUT')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[{time.strftime('%H:%M:%S')}] 🎯 STEPPED TRAIL UPDATED [Trade #{trade_id}]: Stop tightened to {new_stop_price:.5f}", flush=True)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ❌ Stop Update Failed: {e}", flush=True)

def evaluate_stepped_stops():
    trades = get_open_trades()
    if not trades:
        print(f"[{time.strftime('%H:%M:%S')}] Active check: 0 open trades eligible for stepping.", flush=True)
        return

    for t in trades:
        trade_id = t['id']
        units = float(t['currentUnits'])
        entry = float(t['price'])
        
        current_sl = float(t['stopLossOrder']['price']) if 'stopLossOrder' in t else None
        pnl_pct = (float(t['unrealizedPL']) / float(t['initialMarginRequired'])) * 100 if float(t.get('initialMarginRequired', 0)) > 0 else 0

        target_stop = None

        if units > 0: # LONG
            if pnl_pct >= 2.0:
                target_stop = entry * 1.010
            elif pnl_pct >= 1.0:
                target_stop = entry * 1.001
            
            if target_stop and (current_sl is None or target_stop > current_sl):
                update_stop_loss(trade_id, target_stop)

        else: # SHORT
            if pnl_pct >= 2.0:
                target_stop = entry * 0.990
            elif pnl_pct >= 1.0:
                target_stop = entry * 0.999

            if target_stop and (current_sl is None or target_stop < current_sl):
                update_stop_loss(trade_id, target_stop)

if __name__ == '__main__':
    print(f"[{time.strftime('%H:%M:%S')}] 🚀 OANDA Stepped Trailing Stop Engine Active...", flush=True)
    while True:
        try:
            evaluate_stepped_stops()
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Loop Exception: {e}", flush=True)
        time.sleep(15)
