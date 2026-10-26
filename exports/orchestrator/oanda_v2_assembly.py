import os
import sys
import time
import json
import urllib.request
import logging
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "REDACTED_OANDA_TOKEN")
BASE_URL = "https://api-fxpractice.oanda.com/v3"

class OandaInstitutionalClient:
    @staticmethod
    def get_account_summary():
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())['account']
        except Exception as e:
            logging.error(f"❌ Account Summary Error: {e}")
            return None

    @staticmethod
    def get_open_positions_and_trades(instrument="USD_JPY"):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/openTrades"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
        try:
            with urllib.request.urlopen(req) as resp:
                trades = json.loads(resp.read().decode()).get('trades', [])
                for t in trades:
                    if t['instrument'] == instrument:
                        return True, t
                return False, None
        except Exception as e:
            logging.error(f"❌ Trade Check Error: {e}")
            return True, None

    @staticmethod
    def get_candles_and_pricing(instrument="USD_JPY", count=40):
        c_url = f"{BASE_URL}/instruments/{instrument}/candles?count={count}&granularity=M1"
        p_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/pricing?instruments={instrument}"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        
        try:
            req_c = urllib.request.Request(c_url, headers=headers)
            req_p = urllib.request.Request(p_url, headers=headers)
            
            with urllib.request.urlopen(req_c) as resp:
                data = json.loads(resp.read().decode())['candles']
                records = [{
                    'close': float(c['mid']['c']),
                    'high': float(c['mid']['h']),
                    'low': float(c['mid']['l']),
                    'volume': int(c['volume'])
                } for c in data if c['complete']]
                df = pd.DataFrame(records)
                
            with urllib.request.urlopen(req_p) as resp:
                price_data = json.loads(resp.read().decode())['prices'][0]
                bid = float(price_data['bids'][0]['price'])
                ask = float(price_data['asks'][0]['price'])
                spread = ask - bid
                
            return df, spread, bid, ask
        except Exception as e:
            logging.error(f"❌ Data Stream Error: {e}")
            return pd.DataFrame(), 0, 0, 0

    @staticmethod
    def place_institutional_order(instrument, units, sl_price, tp_price):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders"
        order_body = {
            "order": {
                "units": str(int(units)),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{sl_price:.3f}"},
                "takeProfitOnFill": {"price": f"{tp_price:.3f}"}
            }
        }
        data = json.dumps(order_body).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode())
                logging.info(f"🚀 HYBRID SL/TP TRADE EXECUTED! Fill ID: {res.get('orderFillTransaction', {}).get('id', 'FILLED')} | Units: {units}")
                return res
        except Exception as e:
            logging.error(f"❌ Order Rejected: {e}")
            return None

def run_hybrid_engine(instrument="USD_JPY"):
    account = OandaInstitutionalClient.get_account_summary()
    if not account:
        return

    # Check Active Open Positions
    is_open, active_trade = OandaInstitutionalClient.get_open_positions_and_trades(instrument)
    if is_open:
        logging.info(f"🔒 POSITION ACTIVE ({instrument}): Managing trade lifecycle with Hybrid SL/TP bounds.")
        return

    df, spread, bid, ask = OandaInstitutionalClient.get_candles_and_pricing(instrument)
    if df.empty or len(df) < 25:
        return

    # Technical Structure Calculation
    df['sma'] = df['close'].rolling(20).mean()
    df['std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['sma'] + (1.50 * df['std'])
    df['bb_lower'] = df['sma'] - (1.50 * df['std'])
    
    # Dynamic ATR & Structural Liquidity Envelopes
    raw_atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]
    atr = max(raw_atr, 0.020) if not pd.isna(raw_atr) else 0.020
    
    recent_low = df['low'].tail(15).min()
    recent_high = df['high'].tail(15).max()

    # Spread Protection Filter
    if spread > 0.025:
        logging.info(f"🛡️ SPREAD SAFETY: Spread ({spread:.3f}) exceeds 2.5 pip ceiling.")
        return

    latest = df.iloc[-1]
    close = latest['close']

    units = 7500
    # BUY SETUP: Structural SL & Hybrid Expansion TP
    if close <= latest['bb_lower']:
        # Structural Stop Loss
        sl = min(recent_low - (spread + 0.2 * atr), close - (1.5 * atr))
        risk_dist = abs(close - sl)
        
        # Hybrid Take Profit: Max of Opposing High, 1.8x ATR, or 1.5x Risk Distance
        structural_target = recent_high - close
        tp_dist = max(structural_target, 1.8 * atr, 1.5 * risk_dist)
        tp = close + tp_dist
        
        rr_ratio = tp_dist / risk_dist if risk_dist > 0 else 1.5
        logging.info(f"⚡ HYBRID BUY TRIGGER: {instrument} @ {close:.3f} | SL: {sl:.3f} | Target TP: {tp:.3f} | R:R Payoff: {rr_ratio:.2f}x")
        OandaInstitutionalClient.place_institutional_order(instrument, units, sl, tp)

    # SELL SETUP: Structural SL & Hybrid Expansion TP
    elif close >= latest['bb_upper']:
        # Structural Stop Loss
        sl = max(recent_high + (spread + 0.2 * atr), close + (1.5 * atr))
        risk_dist = abs(sl - close)
        
        # Hybrid Take Profit: Max of Opposing Low, 1.8x ATR, or 1.5x Risk Distance
        structural_target = close - recent_low
        tp_dist = max(structural_target, 1.8 * atr, 1.5 * risk_dist)
        tp = close - tp_dist
        
        rr_ratio = tp_dist / risk_dist if risk_dist > 0 else 1.5
        logging.info(f"⚡ HYBRID SELL TRIGGER: {instrument} @ {close:.3f} | SL: {sl:.3f} | Target TP: {tp:.3f} | R:R Payoff: {rr_ratio:.2f}x")
        OandaInstitutionalClient.place_institutional_order(instrument, -units, sl, tp)

    else:
        logging.info(f"👀 Scanning ({instrument}): {close:.3f} | Structural S/R: Low {recent_low:.3f} / High {recent_high:.3f}")

if __name__ == "__main__":
    run_hybrid_engine("USD_JPY")
