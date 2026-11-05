import os, time, math, logging, requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from notifier import send_alert

LOG_PATH = "/home/moonrider1968/bots/killer_beast_sniper/logs/oanda_forex_scalper.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)

ENV_FILE = '/home/moonrider1968/bots/killer_beast_sniper/.env'

def get_config():
    load_dotenv(ENV_FILE, override=True)
    return {
        'api_key': os.getenv('OANDA_API_KEY', '').strip(),
        'account_id': os.getenv('OANDA_ACCOUNT_ID', '').strip(),
        'url': os.getenv('OANDA_URL', 'https://api-fxpractice.oanda.com').strip()
    }

class PairOptimizedOandaEngine:
    def __init__(self):
        self.config = get_config()
        self.session = requests.Session()

        self.retest_pairs = {
            'EUR_USD': {'tp_mult': 2.5, 'min_er': 0.50, 'max_spread_pips': 1.8},
            'USD_CAD': {'tp_mult': 2.0, 'min_er': 0.50, 'max_spread_pips': 2.2},
            'USD_JPY': {'tp_mult': 2.0, 'min_er': 0.50, 'max_spread_pips': 2.0}
        }
        self.fade_pairs = {
            'GBP_JPY': {'max_spread_pips': 3.5},
            'EUR_JPY': {'max_spread_pips': 3.0}
        }
        self.all_instruments = list(self.retest_pairs.keys()) + list(self.fade_pairs.keys())

        self.cooldowns = {}
        self.in_flight = set()
        self.cycle_count = 0

    def get_pip(self, inst):
        return 0.01 if "JPY" in inst else 0.0001

    def get_headers(self):
        return {
            'Authorization': f"Bearer {self.config['api_key']}",
            'Content-Type': 'application/json'
        }

    def api_get(self, endpoint, params=None):
        try:
            r = self.session.get(f"{self.config['url']}{endpoint}", headers=self.get_headers(), params=params, timeout=8)
            if r.status_code == 401:
                logging.error("❌ OANDA 401 Unauthorized. Reloading .env...")
                self.config = get_config()
            return r
        except Exception as e:
            logging.warning(f"⚠️ OANDA GET error ({endpoint}): {e}")
            return None

    def api_post(self, endpoint, data=None):
        try:
            return self.session.post(f"{self.config['url']}{endpoint}", headers=self.get_headers(), json=data, timeout=8)
        except Exception as e:
            logging.warning(f"⚠️ OANDA POST error ({endpoint}): {e}")
            return None

    def get_active_trade_instruments(self):
        r = self.api_get(f"/v3/accounts/{self.config['account_id']}/openTrades")
        if r and r.status_code == 200:
            trades = r.json().get('trades', [])
            return {t.get('instrument') for t in trades if t.get('instrument')}
        return set()

    def get_nav(self):
        r = self.api_get(f"/v3/accounts/{self.config['account_id']}/summary")
        if r and r.status_code == 200:
            return float(r.json().get('account', {}).get('NAV', 62000.0))
        return 62000.0

    def get_candles(self, instrument, granularity="H1", count=60):
        r = self.api_get(f"/v3/instruments/{instrument}/candles", params={'granularity': granularity, 'count': count, 'price': 'MBA'})
        if r and r.status_code == 200:
            candles = r.json().get('candles', [])
            return [c for c in candles if c.get('complete', False)]
        return []

    def run_cycle(self):
        self.cycle_count += 1
        now_ts = time.time()
        nav = self.get_nav()
        active_trades = self.get_active_trade_instruments()

        for inst in self.all_instruments:
            if inst in active_trades or inst in self.in_flight: continue
            if inst in self.cooldowns and now_ts < self.cooldowns[inst]: continue

            candles = self.get_candles(inst, granularity="H1", count=60)
            if len(candles) < 55: continue

            pip = self.get_pip(inst)
            mid_c = [float(c['mid']['c']) for c in candles]
            mid_h = [float(c['mid']['h']) for c in candles]
            mid_l = [float(c['mid']['l']) for c in candles]
            bid_c = [float(c['bid']['c']) for c in candles]
            ask_c = [float(c['ask']['c']) for c in candles]

            # Macro News / Liquidity Spread Check
            current_spread_pips = (ask_c[-1] - bid_c[-1]) / pip
            max_allowed_spread = (self.retest_pairs.get(inst, {}).get('max_spread_pips') or
                                  self.fade_pairs.get(inst, {}).get('max_spread_pips', 3.0))

            if current_spread_pips > max_allowed_spread:
                logging.warning(f"⚠️ [{inst}] Wide Spread ({current_spread_pips:.1f} > {max_allowed_spread} pips). Skipping entry (Macro news event filter).")
                continue

            # 20-Bar Kaufman Path Efficiency
            net_disp = abs(mid_c[-1] - mid_c[-21])
            total_path = sum(abs(mid_c[j] - mid_c[j-1]) for j in range(len(mid_c)-20, len(mid_c)))
            er = net_disp / (total_path + 1e-9)

            # 14-Bar ATR
            trs = [max(mid_h[j]-mid_l[j], abs(mid_h[j]-mid_c[j-1]), abs(mid_l[j]-mid_c[j-1])) for j in range(len(mid_c)-14, len(mid_c))]
            atr = sum(trs) / 14.0

            c = mid_c[-1]
            prev_c = mid_c[-2]
            dollar_risk = nav * 0.015
            decimals = 3 if "JPY" in inst else 5

            # Retest Breakout
            if inst in self.retest_pairs:
                cfg = self.retest_pairs[inst]
                tp_multiplier = cfg['tp_mult']
                min_er = cfg['min_er']

                chan_h = max(mid_h[-52:-2])
                chan_l = min(mid_l[-52:-2])

                if prev_c > chan_h and c <= chan_h and er > min_er and c > mid_c[-2]:
                    risk_dist = max(atr * 1.2, 30.0 * pip)
                    units = min(50000, max(2000, int(dollar_risk / (risk_dist + 1e-9))))
                    entry = ask_c[-1]
                    sl = round(entry - risk_dist, decimals)
                    tp = round(entry + (risk_dist * tp_multiplier), decimals)

                    self.in_flight.add(inst)
                    logging.info(f"🎯 [{inst}] H1 RETEST BULL BREAKOUT | ER: {er:.2f} | Units: {units} | SL: {sl} | TP: {tp}")
                    if self.place_bracket_order(inst, units, "long", sl, tp):
                        msg = f"⚡ **OANDA FOREX BUY FILLED**\nPair: `{inst}`\nStrategy: `H1 Retest Breakout` (ER: {er:.2f})\nUnits: `{units}`\nSL: `{sl}` | TP: `{tp}` ({tp_multiplier}R)"
                        send_alert(msg, title="🎯 FOREX RETEST ORDER FILLED")
                        self.cooldowns[inst] = now_ts + 7200
                    self.in_flight.discard(inst)
                    continue

                elif prev_c < chan_l and c >= chan_l and er > min_er and c < mid_c[-2]:
                    risk_dist = max(atr * 1.2, 30.0 * pip)
                    units = min(50000, max(2000, int(dollar_risk / (risk_dist + 1e-9))))
                    entry = bid_c[-1]
                    sl = round(entry + risk_dist, decimals)
                    tp = round(entry - (risk_dist * tp_multiplier), decimals)

                    self.in_flight.add(inst)
                    logging.info(f"🎯 [{inst}] H1 RETEST BEAR BREAKDOWN | ER: {er:.2f} | Units: {units} | SL: {sl} | TP: {tp}")
                    if self.place_bracket_order(inst, units, "short", sl, tp):
                        msg = f"⚡ **OANDA FOREX SELL FILLED**\nPair: `{inst}`\nStrategy: `H1 Retest Breakdown` (ER: {er:.2f})\nUnits: `{units}`\nSL: `{sl}` | TP: `{tp}` ({tp_multiplier}R)"
                        send_alert(msg, title="🎯 FOREX RETEST ORDER FILLED")
                        self.cooldowns[inst] = now_ts + 7200
                    self.in_flight.discard(inst)
                    continue

            # Guppy 100-Pip Fade
            if inst in self.fade_pairs:
                target_100_pips = 100.0 * pip
                net_move_30 = mid_c[-1] - mid_c[-31]

                if net_move_30 <= -target_100_pips and er < 0.35 and c > prev_c:
                    risk_dist = max(atr * 1.5, 35.0 * pip)
                    units = min(50000, max(2000, int(dollar_risk / (risk_dist + 1e-9))))
                    entry = ask_c[-1]
                    sl = round(entry - risk_dist, decimals)
                    tp = round(entry + (risk_dist * 1.5), decimals)

                    self.in_flight.add(inst)
                    logging.info(f"🎯 [{inst}] GUPPY 100-PIP DUMP FADE | ER: {er:.2f} | Units: {units} | SL: {sl} | TP: {tp}")
                    if self.place_bracket_order(inst, units, "long", sl, tp):
                        msg = f"🦈 **OANDA GUPPY 100-PIP FADE (BUY)**\nPair: `{inst}`\nUnits: `{units}`\nSL: `{sl}` | TP: `{tp}` (1.5R)"
                        send_alert(msg, title="🎯 FOREX GUPPY FADE FILLED")
                        self.cooldowns[inst] = now_ts + 7200
                    self.in_flight.discard(inst)

                elif net_move_30 >= target_100_pips and er < 0.35 and c < prev_c:
                    risk_dist = max(atr * 1.5, 35.0 * pip)
                    units = min(50000, max(2000, int(dollar_risk / (risk_dist + 1e-9))))
                    entry = bid_c[-1]
                    sl = round(entry + risk_dist, decimals)
                    tp = round(entry - (risk_dist * 1.5), decimals)

                    self.in_flight.add(inst)
                    logging.info(f"🎯 [{inst}] GUPPY 100-PIP PUMP FADE | ER: {er:.2f} | Units: {units} | SL: {sl} | TP: {tp}")
                    if self.place_bracket_order(inst, units, "short", sl, tp):
                        msg = f"🦈 **OANDA GUPPY 100-PIP FADE (SELL)**\nPair: `{inst}`\nUnits: `{units}`\nSL: `{sl}` | TP: `{tp}` (1.5R)"
                        send_alert(msg, title="🎯 FOREX GUPPY FADE FILLED")
                        self.cooldowns[inst] = now_ts + 7200
                    self.in_flight.discard(inst)

        if self.cycle_count % 30 == 0:
            logging.info(f"💓 [PAIR-OPTIMIZED FOREX HEARTBEAT] NAV: ${nav:,.2f} | Active Positions ({len(active_trades)}): {list(active_trades)}")

    def place_bracket_order(self, inst, units, side, sl_price, tp_price):
        order_units = units if side == "long" else -units
        decimals = 3 if "JPY" in inst else 5
        order_body = {
            "order": {
                "instrument": inst,
                "units": str(order_units),
                "type": "MARKET",
                "positionFill": "DEFAULT",
                "takeProfitOnFill": {"price": f"{tp_price:.{decimals}f}", "timeInForce": "GTC"},
                "stopLossOnFill": {"price": f"{sl_price:.{decimals}f}", "timeInForce": "GTC"}
            }
        }
        r = self.api_post(f"/v3/accounts/{self.config['account_id']}/orders", data=order_body)
        if r and r.status_code == 201:
            logging.info(f"⚡ [OANDA GTC BRACKET FILLED] {side.upper()} {units} {inst} | SL: {sl_price} | TP: {tp_price}")
            return True
        return False

    def start(self):
        logging.info("=" * 85)
        logging.info("🚀 OANDA PAIR-OPTIMIZED ENGINE ONLINE (NOTIFIER & SPREAD GUARD ARMED)")
        logging.info("   • EUR_USD: H1 Trend Retest (ER > 0.50) | 2.5R TP")
        logging.info("   • USD_CAD: H1 Trend Retest (ER > 0.50) | 2.0R TP")
        logging.info("   • USD_JPY: H1 Trend Retest (ER > 0.50) | 2.0R TP")
        logging.info("   • GBP_JPY / EUR_JPY: Guppy 100-Pip Low-Efficiency Fade | 1.5R TP")
        logging.info("   • Safety: Dynamic Macro News Spread Spike Protection Active")
        logging.info("=" * 85)
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                logging.error(f"Error in Forex cycle: {e}")
            time.sleep(30)

if __name__ == "__main__":
    PairOptimizedOandaEngine().start()
