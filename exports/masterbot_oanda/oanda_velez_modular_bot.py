import os, time, logging, csv, random, sys
from datetime import datetime, date, timezone
from collections import deque
from dotenv import load_dotenv

from oandapyV20 import API
import oandapyV20.endpoints.accounts as accounts
from oandapyV20.endpoints.orders import OrderCreate
from oandapyV20.endpoints.trades import TradeClose
from oandapyV20.endpoints.pricing import PricingStream

# --- Configuration ---
load_dotenv("oanda_velez.env")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
ACCESS_TOKEN = os.getenv("OANDA_ACCESS_TOKEN")
ENV = os.getenv("OANDA_ENV", "practice")

# --- Micro Edge 5.7.1 Parameters ---
RISK_PER_TRADE_PCT = 0.01      
RR_RATIO = 3.0                 # 1:3 (3R) Goal
DAILY_PROFIT_GOAL_PCT = 0.0075 
MAX_TRADES_PER_DAY = 25
COOLDOWN_SECONDS = 1800        

api = API(access_token=ACCESS_TOKEN, environment=ENV)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s',
                    handlers=[logging.FileHandler("oanda_velez_bot.log"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

WATCHLIST = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY", "EUR_GBP"]

class MainBrain:
    def __init__(self):
        self.indicator = IndicatorEngine()
        self.sector = SectorEngine(self)
        self.performance = PerformanceTracker()
        self.active_positions = {}; self.virtual_positions = {}; self.last_trade_time = {}
        self.daily_trades = 0; self.realized_pnl_today = 0.0; self.daily_goal_hit = False
        self.daily_starting_nav = self.get_real_nav(); self.last_trade_date = date.today()
        self.last_heartbeat = 0
        
        self.engines = {
            "Velez": VelezEngine(self.indicator, self.sector),
            "SMC": ICT_SMCEngine(self.indicator, self.sector),
            "Brooks": AlBrooksEngine(self.indicator, self.sector),
            "ORB": ORBEngine(self.indicator, self.sector),
            "Wyckoff": WyckoffEngine(self.indicator, self.sector)
        }
        log.info(f"🚀 VAULT 5.7.1: 1R VIRTUAL TRAIL ACTIVE")

    def get_real_nav(self):
        try:
            r = accounts.AccountSummary(ACCOUNT_ID); resp = api.request(r)
            return float(resp.get('account', {}).get('NAV', 1000.0))
        except: return 1000.0

    def phase_1_ingest(self, msg):
        inst = msg['instrument']
        self.indicator.update(inst, float(msg['closeoutBid']), float(msg['closeoutAsk']))
        return inst

    def phase_2_analyze(self, inst):
        self.sector.update_bias(inst)
        return self.indicator.get_stats(inst)

    def phase_3_filter(self, inst, stats, msg):
        now = time.time()
        if self.last_trade_date != date.today():
            self.daily_trades = 0; self.last_trade_date = date.today(); self.realized_pnl_today = 0.0
            self.daily_goal_hit = False; self.daily_starting_nav = self.get_real_nav()
            self.active_positions.clear(); self.virtual_positions.clear()

        if self.daily_goal_hit or not stats['ready'] or self.daily_trades >= MAX_TRADES_PER_DAY: return None
        if inst in self.active_positions or (now - self.last_trade_time.get(inst, 0) < COOLDOWN_SECONDS): return None
        if self.performance.is_blacklisted(inst): return None

        # Micro-Touch Trigger
        dist_to_ema = abs(stats['price'] - stats['ema20'])
        if dist_to_ema > (stats['atr'] * 0.10): return None 

        names = list(self.engines.keys()); random.shuffle(names)
        for name in names:
            sig = self.engines[name].generate_signal(msg)
            if sig:
                sig.update({'system': name, 'atr': stats['atr'], 'ask': float(msg['closeoutAsk']), 'bid': float(msg['closeoutBid'])})
                return sig
        return None

    def phase_4_dispatch(self, inst, sig):
        nav = self.get_real_nav(); atr = sig['atr']
        precision = 3 if "JPY" in inst else 5
        sl_dist = atr * 1.5
        tp_dist = sl_dist * RR_RATIO 
        
        units = int((nav * RISK_PER_TRADE_PCT) / sl_dist)
        units = max(100, min(units, int(nav * 0.05 / sig['ask'])))

        entry = sig['ask'] if sig['side'] == 'buy' else sig['bid']
        sl = entry - sl_dist if sig['side'] == 'buy' else entry + sl_dist
        tp = entry + tp_dist if sig['side'] == 'buy' else entry - tp_dist
            
        fmt = "{:." + str(precision) + "f}"
        order = {"order": {"units": str(units if sig['side'] == 'buy' else -units), "instrument": inst, "timeInForce": "IOC", "type": "MARKET", "stopLossOnFill": {"price": fmt.format(sl)}, "takeProfitOnFill": {"price": fmt.format(tp)}, "positionFill": "DEFAULT"}}
        try:
            r = OrderCreate(ACCOUNT_ID, data=order); resp = api.request(r)
            t_id = resp.get('orderFillTransaction', {}).get('tradeOpened', {}).get('tradeID')
            if t_id:
                self.daily_trades += 1; self.active_positions[inst] = t_id
                self.last_trade_time[inst] = time.time()
                # Store original risk distance (R) for the trail
                self.virtual_positions[inst] = {
                    'side': sig['side'], 'entry': entry, 'sl': sl, 'tp': tp, 
                    'qty': units, 'system': sig['system'], 'r_dist': sl_dist
                }
                log.info(f"🎯 EXEC {inst} | {sig['system']} | 3R Sniper Set")
        except Exception as e: log.error(f"❌ Dispatch Fail: {e}")

    def check_virtual_management(self):
        unrealized = 0.0
        for inst in list(self.virtual_positions.keys()):
            if inst not in self.active_positions: continue
            stats = self.indicator.get_stats(inst)
            if not stats['ready']: continue
            v = self.virtual_positions[inst]; curr = stats['price']
            pnl_per_unit = (curr - v['entry']) if v['side'] == 'buy' else (v['entry'] - curr)
            
            # --- 1R VIRTUAL TRAILING LOGIC ---
            if pnl_per_unit >= v['r_dist']:
                # Price has hit 1R. Calculate the new trailing SL (1R behind current price)
                new_sl = curr - v['r_dist'] if v['side'] == 'buy' else curr + v['r_dist']
                
                # Only move SL forward, never backward
                if v['side'] == 'buy':
                    if new_sl > v['sl']:
                        v['sl'] = new_sl
                        log.info(f"📈 TRAIL UP: {inst} New SL @ {v['sl']:.5f}")
                else:
                    if new_sl < v['sl']:
                        v['sl'] = new_sl
                        log.info(f"📉 TRAIL DOWN: {inst} New SL @ {v['sl']:.5f}")

            # Check for Stop Out
            if (v['side'] == 'buy' and curr <= v['sl']) or (v['side'] == 'sell' and curr >= v['sl']):
                self.close_trade(inst, "VIRTUAL TRAIL HIT")
                continue

            pnl = pnl_per_unit * v['qty']
            if "JPY" in inst: pnl /= 150.0 
            unrealized += pnl

        if (self.realized_pnl_today + unrealized) >= (self.daily_starting_nav * DAILY_PROFIT_GOAL_PCT):
            if not self.daily_goal_hit:
                log.info(f"🏆 VAULT LOCKED"); self.daily_goal_hit = True
                for i in list(self.active_positions.keys()): self.close_trade(i, "GOAL REACHED")

    def close_trade(self, inst, reason):
        if inst in self.active_positions:
            t_id = self.active_positions.pop(inst); v = self.virtual_positions.pop(inst)
            try:
                api.request(TradeClose(ACCOUNT_ID, t_id)); exit_p = self.indicator.get_stats(inst)['price']
                pnl = (exit_p - v['entry']) * v['qty'] if v['side'] == 'buy' else (v['entry'] - exit_p) * v['qty']
                if "JPY" in inst: pnl /= 150.0
                self.realized_pnl_today += pnl
                self.performance.record(inst, pnl)
                log.info(f"🔴 CLOSED {inst} | {reason} | PnL: ${pnl:+.2f}")
            except Exception as e: log.error(f"❌ Close Fail: {e}")

    def run(self):
        log.info("📡 Trailing Hybrid Stream Starting...")
        while True:
            try:
                stream = PricingStream(ACCOUNT_ID, params={"instruments": ",".join(WATCHLIST)})
                for msg in api.request(stream):
                    if msg["type"] == "PRICE":
                        inst = self.phase_1_ingest(msg); stats = self.phase_2_analyze(inst)
                        sig = self.phase_3_filter(inst, stats, msg)
                        if sig: self.phase_4_dispatch(inst, sig)
                        self.check_virtual_management()
                    if time.time() - self.last_heartbeat > 60:
                        log.info(f"💓 {'LOCKED' if self.daily_goal_hit else 'HUNTING'} | Today: ${self.realized_pnl_today:+.2f}")
                        self.last_heartbeat = time.time()
            except Exception as e: log.error(f"📡 Recovery: {e}"); time.sleep(5)

class PerformanceTracker:
    def __init__(self): self.ledger = {ins: deque(maxlen=10) for ins in WATCHLIST}
    def record(self, ins, pnl): self.ledger[ins].append(1 if pnl > 0 else -1)
    def is_blacklisted(self, ins):
        history = list(self.ledger[ins])
        if len(history) >= 3 and sum(history[-3:]) == -3: return True
        return False

class IndicatorEngine:
    def __init__(self): self.history = {ins: deque(maxlen=300) for ins in WATCHLIST}
    def update(self, ins, bid, ask): self.history[ins].append(bid)
    def get_stats(self, ins):
        p = list(self.history[ins])
        if len(p) < 200: return {'ready': False}
        sma = sum(p[-50:])/50; ema20 = sum(p[-20:])/20
        slope = sma - (sum(p[-100:-50])/50)
        atr = max(p[-50:]) - min(p[-50:])
        return {'ready': True, 'sma': sma, 'ema20': ema20, 'slope': slope, 'price': p[-1], 'atr': max(atr, 0.0010)}

class SectorEngine:
    def __init__(self, brain): self.brain = brain
    def update_bias(self, ins): pass
    def get_bias(self, ins): 
        s = self.brain.indicator.get_stats(ins)
        return "bullish" if s['slope'] > 0 else "bearish"

class BaseEngine:
    def __init__(self, ind, sec): self.ind, self.sec = ind, sec
    def get_ctx(self, p): inst = p['instrument']; return inst, self.ind.get_stats(inst), self.sec.get_bias(inst)

class VelezEngine(BaseEngine):
    def generate_signal(self, p):
        i, s, b = self.get_ctx(p)
        if s['ready'] and b == "bullish" and s['slope'] > 0.0001 and s['price'] > s['ema20']: return {'side': 'buy'}
        if s['ready'] and b == "bearish" and s['slope'] < -0.0001 and s['price'] < s['ema20']: return {'side': 'sell'}
        return None
class ICT_SMCEngine(BaseEngine):
    def generate_signal(self, p):
        i, s, b = self.get_ctx(p); limit = s['ema20'] - (s['atr'] * 0.2)
        if s['ready'] and b == "bullish" and s['price'] >= limit: return {'side': 'buy'}
        return None
class AlBrooksEngine(BaseEngine):
    def generate_signal(self, p):
        i, s, b = self.get_ctx(p)
        if s['ready'] and b == "bullish" and s['price'] > s['ema20']: return {'side': 'buy'}
        return None
class ORBEngine(BaseEngine):
    def generate_signal(self, p):
        i, s, b = self.get_ctx(p)
        if s['ready'] and s['price'] > (s['ema20'] + (s['atr'] * 0.5)): return {'side': 'buy'}
        return None
class WyckoffEngine(BaseEngine):
    def generate_signal(self, p):
        i, s, b = self.get_ctx(p)
        if s['ready'] and b == "bearish" and s['price'] > (s['ema20'] + (s['atr'] * 1.0)): return {'side': 'sell'}
        return None

if __name__ == "__main__":
    MainBrain().run()
