import json, os, urllib.request, logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "")
BASE_URL = "https://api-fxpractice.oanda.com/v3"
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

def evaluate_news_impulse():
    url = f"{BASE_URL}/instruments/USD_JPY/candles?count=30&granularity=M5"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            candles = json.loads(resp.read().decode())['candles']
            df = pd.DataFrame([{
                'close': float(c['mid']['c']), 
                'open': float(c['mid']['o']),
                'high': float(c['mid']['h']), 
                'low': float(c['mid']['l']),
                'volume': float(c['volume'])
            } for c in candles if c['complete']])
            
        df['vol_sma'] = df['volume'].rolling(10).mean()
        df['atr'] = (df['high'] - df['low']).rolling(14).mean()
        
        latest = df.iloc[-1]
        body = abs(latest['close'] - latest['open'])
        atr = max(latest['atr'], 0.060)
        vol_ratio = latest['volume'] / max(latest['vol_sma'], 1.0)

        # Detect high-volume news expansion candle
        if vol_ratio >= 2.2 and body >= (1.8 * atr):
            is_buy = latest['close'] > latest['open']
            sl = round(latest['close'] - (1.0 * atr) if is_buy else latest['close'] + (1.0 * atr), 3)
            tp = round(latest['close'] + (3.0 * atr) if is_buy else latest['close'] - (3.0 * atr), 3)
            
            proposal = {
                "expert_id": "BOT-6: NEWS IMPULSE",
                "units": 10000 if is_buy else -10000,
                "sl": sl,
                "tp": tp,
                "reasoning": f"Volume spike ({vol_ratio:.1f}x) with expanded ATR body ({body:.3f})"
            }
            proposals = []
            if os.path.exists(PROPOSALS_FILE):
                try:
                    with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
                except Exception: proposals = []
            proposals.append(proposal)
            with open(PROPOSALS_FILE, "w") as f: json.dump(proposals, f, indent=2)
            logging.info(f"💡 [BOT-6: NEWS IMPULSE] Submitted {'BUY' if is_buy else 'SELL'} Proposal")
    except Exception as e:
        pass

if __name__ == "__main__":
    evaluate_news_impulse()
