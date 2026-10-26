import os, sys, time, json, urllib.request, logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "REDACTED_OANDA_TOKEN")
BASE_URL = "https://api-fxpractice.oanda.com/v3"
STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

EXPERT_ID = "BOT-4: TREND PULLBACK"

def get_m5_data(instrument="USD_JPY"):
    url = f"{BASE_URL}/instruments/{instrument}/candles?count=50&granularity=M5"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
            candles = json.loads(resp.read().decode())['candles']
            df = pd.DataFrame([{'close': float(c['mid']['c']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'open': float(c['mid']['o'])} for c in candles if c['complete']])
            return df
    except Exception as e:
        logging.error(f"Error fetching data for {EXPERT_ID}: {e}")
        return pd.DataFrame()

def run_expert(instrument="USD_JPY"):
    if not os.path.exists(STATE_FILE): return
    try:
        with open(STATE_FILE, "r") as f: state = json.load(f)
    except Exception: return

    if state.get("open_trades_count", 0) >= 1: return
    h1_macro = state.get("h1_macro_trend", "NEUTRAL")

    df = get_m5_data(instrument)
    if df.empty or len(df) < 25: return

    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['atr'] = (df['high'] - df['low']).rolling(14).mean()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = latest['close']
    ema = latest['ema20']
    atr = max(latest['atr'], 0.050)

    if h1_macro == "BEARISH" and prev['high'] >= prev['ema20'] and close < ema:
        sl = close + (1.2 * atr)
        tp = close - (2.4 * atr)
        proposal = {
            "expert_id": f"{EXPERT_ID} SELL",
            "units": -10000,
            "sl": round(sl, 3),
            "tp": round(tp, 3),
            "reasoning": f"H1 Bearish Pullback to M5 EMA20 at {ema:.3f}. Rejection confirmed.",
            "timestamp": time.time()
        }
        submit_proposal(proposal)

    elif h1_macro == "BULLISH" and prev['low'] <= prev['ema20'] and close > ema:
        sl = close - (1.2 * atr)
        tp = close + (2.4 * atr)
        proposal = {
            "expert_id": f"{EXPERT_ID} BUY",
            "units": 10000,
            "sl": round(sl, 3),
            "tp": round(tp, 3),
            "reasoning": f"H1 Bullish Pullback to M5 EMA20 at {ema:.3f}. Bounce confirmed.",
            "timestamp": time.time()
        }
        submit_proposal(proposal)

def submit_proposal(proposal):
    proposals = []
    if os.path.exists(PROPOSALS_FILE):
        try:
            with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
        except Exception: proposals = []
    
    if not any(p.get("expert_id") == proposal["expert_id"] for p in proposals):
        proposals.append(proposal)
        with open(PROPOSALS_FILE, "w") as f:
            json.dump(proposals, f, indent=2)
        logging.info(f"💡 [{EXPERT_ID}] Submitted Trade Proposal: {proposal['expert_id']}")

if __name__ == "__main__":
    run_expert("USD_JPY")
