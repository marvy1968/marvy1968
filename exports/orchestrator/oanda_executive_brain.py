import os, sys, time, json, urllib.request, logging
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "REDACTED_OANDA_TOKEN")
BASE_URL = "https://api-fxpractice.oanda.com/v3"

class OandaExecutiveClient:
    @staticmethod
    def get_open_trades(instrument="USD_JPY"):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/openTrades"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
                trades = json.loads(resp.read().decode()).get('trades', [])
                return [t for t in trades if t['instrument'] == instrument]
        except Exception as e:
            logging.error(f"❌ Open Trades Check Error: {e}")
            return [True] # Safety fallback lock

    @staticmethod
    def get_m5_data(instrument="USD_JPY", count=50):
        c_url = f"{BASE_URL}/instruments/{instrument}/candles?count={count}&granularity=M5"
        p_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/pricing?instruments={instrument}"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(c_url, headers=headers)) as resp:
                data = json.loads(resp.read().decode())['candles']
                records = [{
                    'close': float(c['mid']['c']),
                    'high': float(c['mid']['h']),
                    'low': float(c['mid']['l']),
                    'open': float(c['mid']['o'])
                } for c in data if c['complete']]
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
    def place_approved_order(instrument, units, sl, tp, expert_tag, regime):
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
                logging.info(f"✅ [APPROVED | REGIME: {regime}] Executed {expert_tag} | Units: {units} | SL: {sl:.3f} | TP: {tp:.3f}")
                return res
        except Exception as e:
            logging.error(f"❌ Order Execution Error: {e}")
            return None

class MacroExecutiveBrain:
    @staticmethod
    def classify_regime(df):
        df['sma20'] = df['close'].rolling(20).mean()
        df['sma50'] = df['close'].rolling(50).mean()
        df['std20'] = df['close'].rolling(20).std()
        df['atr'] = (df['high'] - df['low']).rolling(14).mean()
        
        latest = df.iloc[-1]
        close = latest['close']
        atr_avg = df['atr'].tail(20).mean()
        
        if latest['atr'] > atr_avg * 1.35:
            return "VOLATILITY_EXPANSION"
        
        sma20_slope = (latest['sma20'] - df['sma20'].iloc[-5]) / 5
        if close > latest['sma50'] and latest['sma20'] > latest['sma50'] and sma20_slope > 0.005:
            return "TRENDING_UP"
        elif close < latest['sma50'] and latest['sma20'] < latest['sma50'] and sma20_slope < -0.005:
            return "TRENDING_DOWN"
        else:
            return "RANGING"

    @staticmethod
    def evaluate_proposal(proposed_bot, regime):
        approval_matrix = {
            "RANGING": ["BOT-2: LIQUIDITY SWEEP", "BOT-3: MEAN REVERSION"],
            "TRENDING_UP": ["BOT-1: STRUCTURAL BREAKOUT", "BOT-4: VOLATILITY EXPANSION", "BOT-5: TREND PULLBACK"],
            "TRENDING_DOWN": ["BOT-1: STRUCTURAL BREAKOUT", "BOT-4: VOLATILITY EXPANSION", "BOT-5: TREND PULLBACK"],
            "VOLATILITY_EXPANSION": ["BOT-1: STRUCTURAL BREAKOUT", "BOT-4: VOLATILITY EXPANSION"]
        }
        allowed_bots = approval_matrix.get(regime, [])
        return any(bot_id in proposed_bot for bot_id in allowed_bots)

def run_executive_orchestrator(instrument="USD_JPY"):
    open_trades = OandaExecutiveClient.get_open_trades(instrument)
    # STRICT 1-POSITION LOCK GUARD
    if len(open_trades) >= 1:
        logging.info(f"🔒 EXECUTIVE LOCK: Active position detected ({len(open_trades)} trade open). Standing by until target exit.")
        return

    df, spread = OandaExecutiveClient.get_m5_data(instrument)
    if df.empty or len(df) < 45 or spread > 0.025:
        return

    regime = MacroExecutiveBrain.classify_regime(df)
    latest = df.iloc[-1]
    close = latest['close']
    atr = max(latest['atr'], 0.040)

    high_15 = df['high'].tail(15).max()
    low_15 = df['low'].tail(15).min()

    proposals = []

    # EXPERT 1: STRUCTURAL BREAKOUT
    units_bot1 = 5000
    if close > high_15:
        proposals.append(("BOT-1: STRUCTURAL BREAKOUT", units_bot1, close - (1.2 * atr), close + (2.4 * atr)))
    elif close < low_15:
        proposals.append(("BOT-1: STRUCTURAL BREAKOUT", -units_bot1, close + (1.2 * atr), close - (2.4 * atr)))

    # EXPERT 2: LIQUIDITY SWEEP HUNTER
    units_bot2 = 3500
    if latest['low'] < low_15 and close > low_15:
        sl = latest['low'] - (spread + 0.1 * atr)
        tp = close + (1.6 * abs(close - sl))
        proposals.append(("BOT-2: LIQUIDITY SWEEP BUY", units_bot2, sl, tp))

    # EXPERT 3: MEAN REVERSION ENVELOPE
    units_bot3 = 2500
    bb_upper = latest['sma20'] + (1.65 * latest['std20'])
    bb_lower = latest['sma20'] - (1.65 * latest['std20'])
    if close <= bb_lower:
        proposals.append(("BOT-3: MEAN REVERSION BUY", units_bot3, close - (1.2 * atr), latest['sma20']))
    elif close >= bb_upper:
        proposals.append(("BOT-3: MEAN REVERSION SELL", -units_bot3, close + (1.2 * atr), latest['sma20']))

    # EXPERT 4: VOLATILITY EXPANSION
    units_bot4 = 4000
    if atr > df['atr'].tail(10).mean() * 1.3:
        if close > df['close'].iloc[-2]:
            proposals.append(("BOT-4: VOLATILITY EXPANSION BUY", units_bot4, close - (1.0 * atr), close + (1.8 * atr)))

    # EXPERT 5: TREND PULLBACK
    units_bot5 = 2000
    if regime == "TRENDING_UP" and close <= latest['sma20'] and close > latest['sma50']:
        proposals.append(("BOT-5: TREND PULLBACK BUY", units_bot5, latest['sma50'] - (0.1 * atr), close + (2.0 * atr)))

    if not proposals:
        logging.info(f"🧠 EXECUTIVE SCAN ({instrument}): Regime [{regime}] | Close: {close:.3f} | Standing By (No Expert Proposals Fired)")
        return

    for bot_tag, u, sl, tp in proposals:
        approved = MacroExecutiveBrain.evaluate_proposal(bot_tag, regime)
        if approved:
            OandaExecutiveClient.place_approved_order(instrument, u, sl, tp, bot_tag, regime)
            return
        else:
            logging.info(f"🚫 [REJECTED BY MACRO BRAIN] {bot_tag} proposed trade, but Macro Regime is [{regime}]. Stand-down enforced.")

if __name__ == "__main__":
    run_executive_orchestrator("USD_JPY")
