import os, time, logging, sqlite3, math
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
import pytz
import oandapyV20
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.positions as positions

# [OANDA MASTER CREDENTIALS]
TOKEN = "REDACTED"
ACCT_ID = "REDACTED_OANDA_ACCOUNT"
api = oandapyV20.API(access_token=TOKEN, environment="practice")

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
log = logging.getLogger(__name__)
DB = "microbeast_datamine.db"
EST = pytz.timezone("US/Eastern")

# ====================== NATIVE MATH INDICATORS ======================
def get_indicators(df):
    c, h, l, v = df['close'], df['high'], df['low'], df['volume']
    tr = np.maximum(h - l, np.maximum(abs(h - c.shift(1)), abs(l - c.shift(1))))
    atr = tr.rolling(14).mean().iloc[-1]
    
    # ADX Approximation
    up, dn = h - h.shift(1), l.shift(1) - l
    pos_dm = np.where((up > dn) & (up > 0), up, 0)
    neg_dm = np.where((dn > up) & (dn > 0), dn, 0)
    smooth_tr = tr.rolling(14).mean()
    pdi = 100 * (pd.Series(pos_dm).rolling(14).mean() / smooth_tr)
    ndi = 100 * (pd.Series(neg_dm).rolling(14).mean() / smooth_tr)
    adx = (100 * abs(pdi - ndi) / (pdi + ndi)).rolling(14).mean().iloc[-1]

    sma20, std20 = c.rolling(20).mean().iloc[-1], c.rolling(20).std().iloc[-1]
    
    # OBV & MFI
    obv = (np.sign(c.diff()) * v).fillna(0).cumsum()
    tp = (h + l + c) / 3
    mf = tp * v
    pos_mf = mf.where(tp > tp.shift(1), 0).rolling(14).sum()
    neg_mf = mf.where(tp < tp.shift(1), 0).rolling(14).sum()
    mfi = 100 - (100 / (1 + (pos_mf / neg_mf))).iloc[-1]

    return {
        'adx': adx, 'atr': atr, 'sma20': sma20, 'sma150': c.rolling(150).mean().iloc[-1],
        'upper_bb': sma20 + (std20 * 2), 'lower_bb': sma20 - (std20 * 2), 
        'obv_val': obv.iloc[-1], 'obv_ma': obv.rolling(10).mean().iloc[-1], 'mfi': mfi
    }

# ====================== DATABASE, RISK & STAIRCASE ======================
def init_db():
    conn = sqlite3.connect(DB); conn.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, symbol TEXT, pnl REAL)"); conn.close()

def save_trade(symbol, pnl):
    conn = sqlite3.connect(DB); conn.execute("INSERT INTO trades VALUES (?, ?, ?)", (datetime.now().isoformat(), symbol, pnl)); conn.commit(); conn.close()

class StaircaseManager:
    def __init__(self): self.active_trades = {}
    RUNGS = [0.0075, 0.015, 0.025, 0.040]
    SCALES = [0.40, 0.30, 0.20, 0.10]

    def process(self, symbol, cur_p, ent_p, side, units):
        if symbol not in self.active_trades: return False, 0
        profit = (cur_p - ent_p) / ent_p if side > 0 else (ent_p - cur_p) / ent_p
        rung = self.active_trades[symbol].get('rung', -1)
        for i, r_val in enumerate(self.RUNGS):
            if profit >= r_val and i > rung:
                self.active_trades[symbol]['rung'] = i
                return True, math.floor(abs(units) * self.SCALES[i])
        return False, 0

def get_size(balance, atr):
    try:
        conn = sqlite3.connect(DB); df = pd.read_sql("SELECT pnl FROM trades ORDER BY ts DESC LIMIT 10", conn); conn.close()
        win_rate = (df['pnl'] > 0).mean() if len(df) >= 5 else 0.5
        mult = 0.5 if win_rate < 0.45 else (1.2 if win_rate > 0.75 else 1.0)
    except: mult = 1.0
    return max(1000, int((balance * 0.02 * mult) / (atr * 2))) if atr > 0 else 1000

# ====================== MAIN ENGINE ======================
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "EUR_JPY", "GBP_JPY"]
SETTLEMENT_TIME = dtime(9, 45)
staircase = StaircaseManager()
init_db()

log.info("🏛️ OANDA OMNI-BEAST v15.6 | FULL FEATURES | ANTIBIOT LIVE")

while True:
    try:
        now = datetime.now(EST).time()
        if now < SETTLEMENT_TIME:
            log.info(f"⏳ SHIELD ACTIVE – ({now})"); time.sleep(60); continue

        r_summary = accounts.AccountSummary(ACCT_ID); api.request(r_summary)
        balance = float(r_summary.response.get('account').get('balance'))
        r_pos = positions.PositionList(ACCT_ID); api.request(r_pos)
        open_positions = r_pos.response.get('positions')

        for pair in PAIRS:
            params = {"count": 150, "granularity": "M5"}
            r_c = instruments.InstrumentsCandles(instrument=pair, params=params); api.request(r_c)
            df = pd.DataFrame([{'close': float(c['mid']['c']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'volume': float(c['volume']), 'open': float(c['mid']['o'])} for c in r_c.response.get('candles')])
            stats = get_indicators(df)
            pos = next((p for p in open_positions if p['instrument'] == pair), None)

            if pos:
                units = int(pos['long']['units']) if int(pos['long']['units']) != 0 else int(pos['short']['units'])
                side = 1 if units > 0 else -1
                avg_p = float(pos['long']['averagePrice'] or pos['short']['averagePrice'])
                
                # 1. Staircase Partial Scale-out
                should_scale, s_qty = staircase.process(pair, df['close'].iloc[-1], avg_p, side, units)
                if should_scale and s_qty > 0:
                    api.request(orders.OrderCreate(ACCT_ID, data={"order": {"units": str(-s_qty * side), "instrument": pair, "type": "MARKET"}}))
                    log.info(f"🚀 SCALING OUT: {pair} | Units: {s_qty}")
                
                # 2. Virtual Stop (Breakout)
                if stats['adx'] < 25:
                    if (side == 1 and df['close'].iloc[-1] < stats['lower_bb']) or (side == -1 and df['close'].iloc[-1] > stats['upper_bb']):
                        api.request(positions.PositionClose(ACCT_ID, instrument=pair, data={"longUnits": "ALL"} if side == 1 else {"shortUnits": "ALL"}))
                        save_trade(pair, float(pos['unrealizedPL']))
                        log.warning(f"🛡️ BREAKOUT STOP: {pair}")
            else:
                side = 0
                if stats['adx'] > 25 and stats['obv_val'] > stats['obv_ma']: # PREDATOR
                    if abs(df['close'].iloc[-1] - df['open'].iloc[-1]) > (stats['atr'] * 2.1) and df['close'].iloc[-1] > stats['sma150']:
                        side = 1 if df['close'].iloc[-1] > stats['sma20'] else -1
                        log.info(f"🎯 PREDATOR FIRE: {pair}")
                elif stats['adx'] < 25: # AIRIBOX
                    if df['close'].iloc[-1] <= stats['lower_bb'] and stats['mfi'] < 30: side = 1; log.info(f"📦 AIRIBOX FIRE: {pair}")
                    elif df['close'].iloc[-1] >= stats['upper_bb'] and stats['mfi'] > 70: side = -1; log.info(f"📦 AIRIBOX FIRE: {pair}")

                if side != 0:
                    qty = get_size(balance, stats['atr'])
                    api.request(orders.OrderCreate(ACCT_ID, data={"order": {"units": str(qty * side), "instrument": pair, "type": "MARKET", "timeInForce": "FOK"}}))
                    staircase.active_trades[pair] = {'rung': -1}

        time.sleep(60)
    except Exception as e: log.error(f"ANTIBIOT ERROR: {e}"); time.sleep(30)
