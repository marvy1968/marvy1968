import json, os, urllib.request, logging
import pandas as pd
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "")
BASE_URL = "https://api-fxpractice.oanda.com/v3"
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

def evaluate_asia_range_breakout():
    url = f"{BASE_URL}/instruments/USD_JPY/candles?count=96&granularity=M5"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            candles = json.loads(resp.read().decode())['candles']
            data = []
            for c in candles:
                if c['complete']:
                    dt = datetime.fromisoformat(c['time'].replace('Z', '+00:00'))
                    data.append({
                        'time': dt,
                        'hour': dt.hour,
                        'close': float(c['mid']['c']),
                        'high': float(c['mid']['h']),
                        'low': float(c['mid']['l'])
                    })
            df = pd.DataFrame(data)

        # Asian Session range (00:00 to 06:00 UTC)
        asia_df = df[(df['hour'] >= 0) & (df['hour'] < 6)]
        if asia_df.empty: return
        
        asia_high = asia_df['high'].max()
        asia_low = asia_df['low'].min()
        latest = df.iloc[-1]
        curr_hour = latest['hour']
        close = latest['close']

        # Trade London Open Breakout (07:00 to 11:00 UTC)
        if 7 <= curr_hour <= 11:
            if close > asia_high + 0.015:
                sl = round(asia_high - 0.080, 3)
                tp = round(close + 0.250, 3)
                proposal = {
                    "expert_id": "BOT-7: ASIA LONDON BREAKOUT",
                    "units": 10000,
                    "sl": sl,
                    "tp": tp,
                    "reasoning": f"Bullish breakout above Asian Session High ({asia_high:.3f})"
                }
            elif close < asia_low - 0.015:
                sl = round(asia_low + 0.080, 3)
                tp = round(close - 0.250, 3)
                proposal = {
                    "expert_id": "BOT-7: ASIA LONDON BREAKOUT",
                    "units": -10000,
                    "sl": sl,
                    "tp": tp,
                    "reasoning": f"Bearish breakout below Asian Session Low ({asia_low:.3f})"
                }
            else:
                return

            proposals = []
            if os.path.exists(PROPOSALS_FILE):
                try:
                    with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
                except Exception: proposals = []
            proposals.append(proposal)
            with open(PROPOSALS_FILE, "w") as f: json.dump(proposals, f, indent=2)
            logging.info("💡 [BOT-7: ASIA LONDON BREAKOUT] Submitted Breakout Proposal")
    except Exception as e:
        pass

if __name__ == "__main__":
    evaluate_asia_range_breakout()
