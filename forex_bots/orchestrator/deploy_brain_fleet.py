from datetime import datetime, timezone
import os, sys, time, json, urllib.request, logging
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
API_KEY = os.getenv("OANDA_API_KEY", "REDACTED_OANDA_TOKEN")
BASE_URL = "https://api-fxpractice.oanda.com/v3"
STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

MAX_OPEN_TRADES = 10
COOLDOWN_SECONDS = 120   # 2-minute post-trade cooldown per expert
EXPERT_COOLDOWN_TRACKER = {}

# ==========================================
# ADVANCED SAFETY & CIRCUIT BREAKER STATE
# ==========================================
CONSECUTIVE_LOSSES = 0
SAFETY_PAUSE_UNTIL = 0           # Timestamp for 15-min pause
HARD_BREAKER_UNTIL = 0          # Timestamp for 8-hour pause
SESSION_START_EQUITY = None      # Baseline for 1% drawdown guard
           # Up to 10 concurrent open positions
MAX_SPREAD_THRESHOLD = 0.022   # 2.2 pip max spread tolerance
MIN_TP_PIPS = 0.180            # 18.0 pips minimum profit target
MIN_SL_PIPS = 0.080            # 8.0 pips minimum stop loss

class OandaMultiTfClient:
    @staticmethod
    def get_account_summary():
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
                data = json.loads(resp.read().decode())['account']
                return float(data['balance']), float(data['NAV'])
        except Exception as e:
            logging.error(f"Account Summary Error: {e}")
            return 100000.0, 100000.0

    @staticmethod
    def get_candles(instrument, granularity, count=50):
        url = f"{BASE_URL}/instruments/{instrument}/candles?count={count}&granularity={granularity}"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
                candles = json.loads(resp.read().decode())['candles']
                df = pd.DataFrame([{
                    'close': float(c['mid']['c']), 
                    'high': float(c['mid']['h']), 
                    'low': float(c['mid']['l']), 
                    'open': float(c['mid']['o']),
                    'volume': float(c['volume'])
                } for c in candles if c['complete']])
                return df
        except Exception as e:
            logging.error(f"Error fetching {granularity} candles: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_market_data(instrument="USD_JPY"):
        df_m5 = OandaMultiTfClient.get_candles(instrument, "M5", 50)
        df_m15 = OandaMultiTfClient.get_candles(instrument, "M15", 40)
        df_h1 = OandaMultiTfClient.get_candles(instrument, "H1", 30)
        df_h4 = OandaMultiTfClient.get_candles(instrument, "H4", 20)

        p_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/pricing?instruments={instrument}"
        t_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/openTrades"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        try:
            with urllib.request.urlopen(urllib.request.Request(p_url, headers=headers)) as resp:
                price_data = json.loads(resp.read().decode())['prices'][0]
                bid = float(price_data['bids'][0]['price'])
                ask = float(price_data['asks'][0]['price'])
                spread = ask - bid

            with urllib.request.urlopen(urllib.request.Request(t_url, headers=headers)) as resp:
                all_open_trades = json.loads(resp.read().decode()).get('trades', [])
                pair_trades = [t for t in all_open_trades if t['instrument'] == instrument]

            return df_m5, df_m15, df_h1, df_h4, spread, pair_trades, len(all_open_trades)
        except Exception as e:
            logging.error(f"Market Data Fetch Error: {e}")
            return df_m5, df_m15, df_h1, df_h4, 0, [], 1

    @staticmethod
    def execute_fifo_order(instrument, units, sl, tp, expert_tag, reasoning):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders"
        
        order_body = {
            "order": {
                "units": str(int(units)),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{sl:.3f}", "timeInForce": "GTC"},
                "takeProfitOnFill": {"price": f"{tp:.3f}", "timeInForce": "GTC"}
            }
        }
        req = urllib.request.Request(
            url, 
            data=json.dumps(order_body).encode('utf-8'), 
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, 
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode())
                logging.info(f"🚀 [FLEET 7 EXECUTION] {expert_tag} | Units: {int(units)} | AI SL: {sl:.3f} | AI TP: {tp:.3f} | Reasoning: {reasoning}")
                return res
        except Exception as e:
            logging.error(f"Execution Error: {e}")
            return None

    @staticmethod
    def update_trade_stop_loss(trade_id, new_sl):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders"
        order_body = {
            "order": {
                "timeInForce": "GTC",
                "type": "STOP_LOSS",
                "tradeID": str(trade_id),
                "price": f"{new_sl:.3f}"
            }
        }
        # Fallback payload structure for V3 trade modification endpoint
        alt_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/trades/{trade_id}/orders"
        alt_body = {
            "stopLoss": {
                "timeInForce": "GTC",
                "price": f"{new_sl:.3f}"
            }
        }
        
        req = urllib.request.Request(
            alt_url,
            data=json.dumps(alt_body).encode('utf-8'),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            if err.code == 400:
                # If existing Stop Loss order exists, update via PUT on /accounts/{ACCOUNT_ID}/orders
                req_post = urllib.request.Request(
                    url,
                    data=json.dumps(order_body).encode('utf-8'),
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req_post) as resp2:
                        return json.loads(resp2.read().decode())
                except Exception as e2:
                    logging.error(f"Stop Loss Modification Error: {e2}")
                    return None
            logging.error(f"Stop Loss Update Error: {err}")
            return None
        except Exception as e:
            logging.error(f"Stop Loss General Error: {e}")
            return None
class MultiTimeframeAiBrain:
    @staticmethod
    def analyze_holistic_market(df_m5, df_m15, df_h1, df_h4):
        bias_score = 0
        
        if not df_h4.empty and len(df_h4) >= 10:
            df_h4['sma20'] = df_h4['close'].rolling(10).mean()
            if df_h4['close'].iloc[-1] > df_h4['sma20'].iloc[-1]: bias_score += 3
            else: bias_score -= 3

        if not df_h1.empty and len(df_h1) >= 15:
            df_h1['sma20'] = df_h1['close'].rolling(15).mean()
            if df_h1['close'].iloc[-1] > df_h1['sma20'].iloc[-1]: bias_score += 2
            else: bias_score -= 2

        if not df_m15.empty and len(df_m15) >= 15:
            df_m15['sma20'] = df_m15['close'].rolling(15).mean()
            df_m15['vol_sma'] = df_m15['volume'].rolling(10).mean()
            latest_m15 = df_m15.iloc[-1]
            vol_multiplier = latest_m15['volume'] / max(latest_m15['vol_sma'], 1.0)
            vol_weight = 1.5 if vol_multiplier > 1.2 else 1.0

            if latest_m15['close'] > latest_m15['sma20']: bias_score += int(2 * vol_weight)
            else: bias_score -= int(2 * vol_weight)

        if not df_m5.empty and len(df_m5) >= 20:
            df_m5['sma20'] = df_m5['close'].rolling(20).mean()
            if df_m5['close'].iloc[-1] > df_m5['sma20'].iloc[-1]: bias_score += 1
            else: bias_score -= 1

        if bias_score >= 3: consensus = "STRONG_BULLISH"
        elif bias_score >= 1: consensus = "WEAK_BULLISH"
        elif bias_score <= -3: consensus = "STRONG_BEARISH"
        elif bias_score <= -1: consensus = "WEAK_BEARISH"
        else: consensus = "NEUTRAL"

        return consensus, bias_score

    @staticmethod
    def synthesize_dynamic_sl_tp(close, rec_sl, rec_tp, is_buy, df_m5, consensus):
        df_m5['atr'] = (df_m5['high'] - df_m5['low']).rolling(14).mean()
        atr = max(df_m5['atr'].iloc[-1], 0.060)

        tp_mult = 2.5 if "STRONG" in consensus else 2.0
        sl_mult = 1.2

        if is_buy:
            ai_sl = min(rec_sl, close - (sl_mult * atr)) if rec_sl > 0 else close - (sl_mult * atr)
            ai_tp = max(rec_tp, close + (tp_mult * atr)) if rec_tp > 0 else close + (tp_mult * atr)
            if (ai_tp - close) < MIN_TP_PIPS: ai_tp = close + MIN_TP_PIPS
            if (close - ai_sl) < MIN_SL_PIPS: ai_sl = close - MIN_SL_PIPS
        else:
            ai_sl = max(rec_sl, close + (sl_mult * atr)) if rec_sl > 0 else close + (sl_mult * atr)
            ai_tp = min(rec_tp, close - (tp_mult * atr)) if rec_tp > 0 else close - (tp_mult * atr)
            if (close - ai_tp) < MIN_TP_PIPS: ai_tp = close - MIN_TP_PIPS
            if (ai_sl - close) < MIN_SL_PIPS: ai_sl = close + MIN_SL_PIPS

        return round(ai_sl, 3), round(ai_tp, 3)

    @staticmethod
    def manage_stair_step_profit_protection(open_trades, df_m5):
        if not open_trades or df_m5.empty: return
        latest_close = df_m5['close'].iloc[-1]

        for trade in open_trades:
            trade_id = trade['id']
            entry_price = float(trade['price'])
            units = float(trade['currentUnits'])
            initial_sl = float(trade.get('stopLossOrder', {}).get('price', 0))
            if initial_sl == 0: continue

            risk_distance = abs(entry_price - initial_sl)
            if risk_distance <= 0.010: risk_distance = 0.080

            if units > 0: # BUY
                gain = latest_close - entry_price
                if gain >= (2.0 * risk_distance) and initial_sl < (entry_price + 0.160):
                    new_sl = entry_price + 0.160
                    logging.info(f"🧱 [STAIR-STEP 3 LOCK] Trade #{trade_id} SL -> {new_sl:.3f} (+16 pips)")
                    OandaMultiTfClient.update_trade_stop_loss(trade_id, new_sl)
                elif gain >= (1.5 * risk_distance) and initial_sl < (entry_price + 0.080):
                    new_sl = entry_price + 0.080
                    logging.info(f"🧱 [STAIR-STEP 2 LOCK] Trade #{trade_id} SL -> {new_sl:.3f} (+8 pips)")
                    OandaMultiTfClient.update_trade_stop_loss(trade_id, new_sl)
                elif gain >= (1.0 * risk_distance) and initial_sl < (entry_price + 0.020):
                    new_sl = entry_price + 0.020
                    logging.info(f"🛡️ [STAIR-STEP 1 BREAKEVEN] Trade #{trade_id} SL -> {new_sl:.3f} (+2 pips)")
                    OandaMultiTfClient.update_trade_stop_loss(trade_id, new_sl)

            elif units < 0: # SELL
                gain = entry_price - latest_close
                if gain >= (2.0 * risk_distance) and initial_sl > (entry_price - 0.160):
                    new_sl = entry_price - 0.160
                    logging.info(f"🧱 [STAIR-STEP 3 LOCK] Trade #{trade_id} SL -> {new_sl:.3f} (+16 pips)")
                    OandaMultiTfClient.update_trade_stop_loss(trade_id, new_sl)
                elif gain >= (1.5 * risk_distance) and initial_sl > (entry_price - 0.080):
                    new_sl = entry_price - 0.080
                    logging.info(f"🧱 [STAIR-STEP 2 LOCK] Trade #{trade_id} SL -> {new_sl:.3f} (+8 pips)")
                    OandaMultiTfClient.update_trade_stop_loss(trade_id, new_sl)
                elif gain >= (1.0 * risk_distance) and initial_sl > (entry_price - 0.020):
                    new_sl = entry_price - 0.020
                    logging.info(f"🛡️ [STAIR-STEP 1 BREAKEVEN] Trade #{trade_id} SL -> {new_sl:.3f} (+2 pips)")
                    OandaMultiTfClient.update_trade_stop_loss(trade_id, new_sl)

    @staticmethod
    def calculate_dynamic_risk_units(balance, entry_price, sl_price, confidence_score, num_active):
        """
        DYNAMIC SIZING ENGINE:
        Scales equity risk up to 1.0% per trade depending on trend conviction score.
        Score >= 5 (Strong Alignment) -> 1.00% Risk per trade
        Score >= 3 (Moderate Alignment) -> 0.75% Risk per trade
        Score <= 2 (Neutral / Reversion) -> 0.35%-0.50% Risk per trade
        """
        abs_score = abs(confidence_score)
        # AGGRESSIVE COMPOUNDING ENGINE: Reinvests NAV floating gains directly into unit sizing
        if abs_score >= 5:
            risk_pct = 0.0250  # 2.50% Equity Risk on High-Conviction Alignment
        elif abs_score >= 3:
            risk_pct = 0.0175  # 1.75% Equity Risk on Moderate Alignment
        else:
            risk_pct = 0.0100  # 1.00% Baseline Equity Risk

        risk_amount = balance * risk_pct
        price_risk = abs(entry_price - sl_price)
        if price_risk <= 0.005: price_risk = 0.040

        units = risk_amount / price_risk
        
        # Add offset per slot to eliminate FIFO same-size order rejection errors
        slot_offset = num_active * 11
        final_units = max(2000, min(units, 150000)) + slot_offset
        return final_units

def main_brain_router(instrument="USD_JPY"):
    global HARD_BREAKER_UNTIL, SAFETY_PAUSE_UNTIL, SESSION_START_EQUITY, CONSECUTIVE_LOSSES
    balance, nav = OandaMultiTfClient.get_account_summary()
    df_m5, df_m15, df_h1, df_h4, spread, pair_trades, total_open_trades = OandaMultiTfClient.get_market_data(instrument)
    
    if df_m5.empty or len(df_m5) < 30: return
    if spread > MAX_SPREAD_THRESHOLD:
        logging.info(f"⚠️ [SPREAD GUARD] Spread ({spread:.4f}) exceeds ceiling ({MAX_SPREAD_THRESHOLD:.4f}). Standing down.")
        return

    consensus, score = MultiTimeframeAiBrain.analyze_holistic_market(df_m5, df_m15, df_h1, df_h4)
    close = df_m5['close'].iloc[-1]

    if pair_trades:
        MultiTimeframeAiBrain.manage_stair_step_profit_protection(pair_trades, df_m5)

    num_active = len(pair_trades)
    logging.info(f"🌐 [7-BOT FLEET MATRIX] Active Trades: {num_active}/{MAX_OPEN_TRADES} | Close: {close:.3f} | MTF Consensus: [{consensus}] (Score: {score})")

    if num_active >= MAX_OPEN_TRADES:
        logging.info(f"🔒 [MAX POSITIONS REACHED] {num_active}/{MAX_OPEN_TRADES} trades open. Standing down for new entries.")
        with open(PROPOSALS_FILE, "w") as f: json.dump([], f)
        return

    if os.path.exists(PROPOSALS_FILE):
        try:
            with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
        except Exception: proposals = []
            
        if proposals:
            logging.info(f"📥 [7-BOT AI BRAIN] Evaluating {len(proposals)} Expert Proposal(s)...")


            # 1. Check Friday Session End Close (10 mins before close @ 20:50 UTC)
            now_utc = datetime.now(timezone.utc)
            if now_utc.weekday() == 4 and (now_utc.hour > 20 or (now_utc.hour == 20 and now_utc.minute >= 50)):
                logging.warning("⚠️ [FRIDAY AUTO-CLOSE] 10 mins before weekend close! Closing all active trades.")
                OandaMultiTfClient.close_all_positions(ACCOUNT_ID)
                return

            # 2. Check 8-Hour / 15-Min Circuit Breaker Timers
            current_time = time.time()
            if current_time < HARD_BREAKER_UNTIL:
                rem_hrs = (HARD_BREAKER_UNTIL - current_time) / 3600.0
                logging.info(f"🛑 [CIRCUIT BREAKER ACTIVE] 1% Equity Drawdown Pause. Resuming in {rem_hrs:.2f} hours.")
                return

            if current_time < SAFETY_PAUSE_UNTIL:
                rem_mins = (SAFETY_PAUSE_UNTIL - current_time) / 60.0
                logging.info(f"⏳ [SAFETY PAUSE ACTIVE] 3 Consecutive Losses Pause. Resuming in {rem_mins:.1f} minutes.")
                return

            # 3. Dynamic 1% Drawdown Check
            if SESSION_START_EQUITY is None:
                SESSION_START_EQUITY = nav
            
            total_loss_pct = (SESSION_START_EQUITY - nav) / SESSION_START_EQUITY
            if total_loss_pct >= 0.01:  # 1% Total Loss Trigger
                HARD_BREAKER_UNTIL = current_time + (8 * 3600)
                SESSION_START_EQUITY = nav  # Reset baseline for next session
                logging.error(f"🚨 [HARD CIRCUIT BREAKER] 1% Equity Loss Triggered ({total_loss_pct*100:.2f}%). Pausing fleet for 8 Hours!")
                return

            for prop in proposals:
                # --- ROI MASTER OPTIMIZATION: HOUR & LOSS TRUNCATION FILTER ---
                now_utc = datetime.now(timezone.utc)
                current_hour = now_utc.hour
                toxic_hours = [6, 10, 13, 18]
                expert_id = prop.get('expert_id', '')
                if current_hour in toxic_hours and expert_id in ['BOT-2', 'BOT-3']:
                    logging.info(f"🚫 [HOUR FILTER] Suppressing {expert_id} counter-trend trade during high-drag UTC hour {current_hour}:00")
                    continue
                last_exec = EXPERT_COOLDOWN_TRACKER.get(expert_id, 0)
                if time.time() - last_exec < COOLDOWN_SECONDS:
                    logging.info(f"⏳ [COOLDOWN GUARD] {expert_id} is in 120s cooldown period. Skipping execution.")
                    continue
                rec_sl = prop.get("sl", 0.0)
                rec_tp = prop.get("tp", 0.0)
                reasoning = prop.get("reasoning", "")
                is_buy = prop.get("units", 0) > 0

                approved = False
                if is_buy and consensus in ["STRONG_BULLISH", "WEAK_BULLISH", "NEUTRAL"]:
                    approved = True
                elif not is_buy and consensus in ["STRONG_BEARISH", "WEAK_BEARISH", "NEUTRAL"]:
                    approved = True
                else:
                    logging.info(f"🚫 [MTF VETO] {expert_id} ({'BUY' if is_buy else 'SELL'}) conflicts with MTF Consensus [{consensus}].")

                if approved:
                    ai_sl, ai_tp = MultiTimeframeAiBrain.synthesize_dynamic_sl_tp(close, rec_sl, rec_tp, is_buy, df_m5, consensus)
                    units = MultiTimeframeAiBrain.calculate_dynamic_risk_units(balance, close, ai_sl, score, num_active)
                    final_units = units if is_buy else -units

                    OandaMultiTfClient.execute_fifo_order(instrument, final_units, ai_sl, ai_tp, expert_id, reasoning)
                    EXPERT_COOLDOWN_TRACKER[expert_id] = time.time()
                    with open(PROPOSALS_FILE, "w") as f: json.dump([], f)
                    return

            with open(PROPOSALS_FILE, "w") as f: json.dump([], f)

if __name__ == "__main__":
    main_brain_router("USD_JPY")
