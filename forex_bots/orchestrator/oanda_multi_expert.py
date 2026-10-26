import os, sys, time, json, urllib.request, logging
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "REDACTED_OANDA_TOKEN")
BASE_URL = "https://api-fxpractice.oanda.com/v3"

class OandaMultiAgentClient:
    @staticmethod
    def get_open_trades(instrument="USD_JPY"):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/openTrades"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
                trades = json.loads(resp.read().decode()).get('trades', [])
                return [t for t in trades if t['instrument'] == instrument]
        except Exception as e:
            return []

    @staticmethod
    def get_m5_data(instrument="USD_JPY", count=40):
        c_url = f"{BASE_URL}/instruments/{instrument}/candles?count={count}&granularity=M5"
        p_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/pricing?instruments={instrument}"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(c_url, headers=headers)) as resp:
                data = json.loads(resp.read().decode())['candles']
                records = [{'close': float(c['mid']['c']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'open': float(c['mid']['o'])} for c in data if c['complete']]
                df = pd.DataFrame(records)
            with urllib.request.urlopen(urllib.request.Request(p_url, headers=headers)) as resp:
                price_data = json.loads(resp.read().decode())['prices'][0]
                bid = float(price_data['bids'][0]['price'])
                ask = float(price_data['asks'][0]['price'])
                spread = ask - bid
            return df, spread
        except Exception:
            return pd.DataFrame(), 0

    @staticmethod
    def place_subbot_order(instrument, units, sl, tp, tag):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders"
        order_body = {
            "order": {
                "units": str(int(units)),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{sl:.3f}"},
                "takeProfitOnFill": {"price": f"{tp:.3f}"}
            }
        }
        req = urllib.request.Request(url, data=json.dumps(order_body).encode('utf-8'), headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode())
                logging.info(f"🚀 [{tag}] ORDER EXECUTED! Units: {units} | SL: {sl:.3f} | TP: {tp:.3f}")
                return res
        except Exception as e:
            logging.error(f"❌ [{tag}] Rejected: {e}")
            return None

def run_multi_agent_ensemble(instrument="USD_JPY"):
    open_trades = OandaMultiAgentClient.get_open_trades(instrument)
    if len(open_trades) >= 2:
        logging.info(f"🔒 ENSEMBLE LOCK: {len(open_trades)} Sub-Bot trades active. Waiting for exit targets.")
        return

    df, spread = OandaMultiAgentClient.get_m5_data(instrument)
    if df.empty or len(df) < 25 or spread > 0.025:
        return

    df['sma20'] = df['close'].rolling(20).mean()
    df['std20'] = df['close'].rolling(20).std()
    df['atr'] = (df['high'] - df['low']).rolling(14).mean()
    
    latest = df.iloc[-1]
    close = latest['close']
    atr = max(latest['atr'], 0.040)
    units_per_bot = 3000

    high_15 = df['high'].tail(15).max()
    low_15 = df['low'].tail(15).min()

    # 1. SUB-BOT 1: STRUCTURAL BREAKOUT (Target: 2.0x R:R)
    if close > high_15:
        sl = close - (1.2 * atr)
        tp = close + (2.4 * atr)
        OandaMultiAgentClient.place_subbot_order(instrument, units_per_bot, sl, tp, "BOT-1: STRUCTURAL BREAKOUT")
        return

    # 2. SUB-BOT 2: LIQUIDITY SWEEP (Target: 1.6x R:R)
    if latest['low'] < low_15 and close > low_15:
        sl = latest['low'] - (spread + 0.1 * atr)
        tp = close + (1.6 * abs(close - sl))
        OandaMultiAgentClient.place_subbot_order(instrument, units_per_bot, sl, tp, "BOT-2: LIQUIDITY SWEEP BUY")
        return

    # 3. SUB-BOT 3: MEAN REVERSION (Target: 20-SMA Mean)
    bb_upper = latest['sma20'] + (1.65 * latest['std20'])
    bb_lower = latest['sma20'] - (1.65 * latest['std20'])
    if close <= bb_lower:
        sl = close - (1.2 * atr)
        tp = latest['sma20']
        OandaMultiAgentClient.place_subbot_order(instrument, units_per_bot, sl, tp, "BOT-3: MEAN REVERSION BUY")
        return
    elif close >= bb_upper:
        sl = close + (1.2 * atr)
        tp = latest['sma20']
        OandaMultiAgentClient.place_subbot_order(instrument, -units_per_bot, sl, tp, "BOT-3: MEAN REVERSION SELL")
        return

    # 4. SUB-BOT 4: VOLATILITY EXPANSION (Target: 1.8x R:R)
    if atr > df['atr'].tail(10).mean() * 1.3:
        if close > df['close'].iloc[-2]:
            sl = close - (1.0 * atr)
            tp = close + (1.8 * atr)
            OandaMultiAgentClient.place_subbot_order(instrument, units_per_bot, sl, tp, "BOT-4: VOLATILITY EXPANSION BUY")
            return

    logging.info(f"👀 ENSEMBLE SCAN ({instrument}): {close:.3f} | Support: {low_15:.3f} | Resistance: {high_15:.3f} | All 5 Expert Sub-Bots Standing By")

if __name__ == "__main__":
    run_multi_agent_ensemble("USD_JPY")
