import json, os, urllib.request, logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "")
BASE_URL = "https://api-fxpractice.oanda.com/v3"
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

def evaluate_carry_drift():
    url = f"{BASE_URL}/instruments/USD_JPY/candles?count=50&granularity=M5"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            candles = json.loads(resp.read().decode())['candles']
            df = pd.DataFrame([{'close': float(c['mid']['c']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l'])} for c in candles if c['complete']])
            
        df['sma50'] = df['close'].rolling(50).mean()
        df['atr'] = (df['high'] - df_low if 'low' in df else df['high'] - df['low']).rolling(14).mean()
        
        close = df['close'].iloc[-1]
        sma50 = df['sma50'].iloc[-1]
        atr = max(df['atr'].iloc[-1], 0.060)

        # Carry drift favors USD (higher yield) when price holds above 50 SMA
        if close > sma50 + 0.020:
            sl = round(close - (1.2 * atr), 3)
            tp = round(close + (2.5 * atr), 3)
            proposal = {
                "expert_id": "BOT-5: MACRO CARRY DRIFT",
                "units": 10000,
                "sl": sl,
                "tp": tp,
                "reasoning": "Positive USD/JPY yield differential drift above 50 SMA"
            }
            proposals = []
            if os.path.exists(PROPOSALS_FILE):
                try:
                    with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
                except Exception: proposals = []
            proposals.append(proposal)
            with open(PROPOSALS_FILE, "w") as f: json.dump(proposals, f, indent=2)
            logging.info("💡 [BOT-5: CARRY DRIFT] Submitted BUY Proposal")
    except Exception as e:
        pass

if __name__ == "__main__":
    evaluate_carry_drift()
