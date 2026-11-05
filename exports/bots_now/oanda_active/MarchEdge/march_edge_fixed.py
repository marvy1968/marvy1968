import os, time, logging, sqlite3, math
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
import pytz
import oandapyV20
from oandapyV20.endpoints.accounts import AccountSummary
from oandapyV20.endpoints.positions import PositionList, PositionClose
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.orders import OrderCreate

# [OANDA MASTER CREDENTIALS]
TOKEN = "REDACTED"
ACCT_ID = "REDACTED_OANDA_ACCOUNT"
api = oandapyV20.API(access_token=TOKEN, environment="practice")

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
log = logging.getLogger(__name__)
DB = "microbeast_datamine.db"
EST = pytz.timezone("US/Eastern")

# ====================== 🧬 MASTER BEAST NATIVE MATH ======================
def get_indicators(df):
    c, h, l, v = df['close'], df['high'], df['low'], df['volume']
    tr = np.maximum(h - l, np.maximum(abs(h - c.shift(1)), abs(l - c.shift(1))))
    atr = tr.rolling(14).mean().iloc[-1]
    up, dn = h - h.shift(1), l.shift(1) - l
    pos_dm = np.where((up > dn) & (up > 0), up, 0)
    neg_dm = np.where((dn > up) & (dn > 0), dn, 0)
    smooth_tr = tr.rolling(14).mean()
    pdi = 100 * (pd.Series(pos_dm).rolling(14).mean() / smooth_tr)
    ndi = 100 * (pd.Series(neg_dm).rolling(14).mean() / smooth_tr)
    adx = (100 * abs(pdi - ndi) / (pdi + ndi)).rolling(14).mean().iloc[-1]
    sma20, std20 = c.rolling(20).mean().iloc[-1], c.rolling(20).std().iloc[-1]
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

def get_size(balance, atr):
    try:
        conn = sqlite3.connect(DB); df = pd.read_sql("SELECT pnl FROM trades ORDER BY ts DESC LIMIT 10", conn); conn.close()
        win_rate = (df['pnl'] > 0).mean() if len(df) >= 5 else 0.5
        mult = 0.5 if win_rate < 0.45 else (1.2 if win_rate > 0.75 else 1.0)
    except: mult = 1.0
    return max(1000, int((balance * 0.0408 * mult) / (atr * 2))) if atr > 0 else 1000

class StaircaseManager:
    def __init__(self): self.active_trades = {}
    RUNGS, SCALES = [0.0075, 0.015, 0.025, 0.040], [0.40, 0.30, 0.20, 0.10]
    def process(self, sym, cp, ep, side, units):
        if sym not in self.active_trades: return False, 0
        profit = (cp - ep) / ep if side > 0 else (ep - cp) / ep
        rung = self.active_trades[sym].get('rung', -1)
        for i, r_val in enumerate(self.RUNGS):
            if profit >= r_val and i > rung:
                self.active_trades[sym]['rung'] = i
                return True, math.floor(abs(units) * self.SCALES[i])
        return False, 0

# ====================== 🏛️ MAIN ENGINE ======================
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "EUR_JPY", "GBP_JPY"]
SETTLEMENT_TIME = dtime(9, 45)
staircase = StaircaseManager()

def init_db():
    conn = sqlite3.connect(DB); conn.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, symbol TEXT, pnl REAL)"); conn.close()
init_db()

log.info("🏛️ OANDA OMNI-BEAST v15.9.4 | HARDENED ANTIBIOT LIVE")

while True:
    try:
        r_summary = AccountSummary(ACCT_ID); api.request(r_summary)
        balance, equity = float(r_summary.response['account']['balance']), float(r_summary.response['account']['NAV'])
        log.info(f"📊 Equity: ${equity:,.2f}")
        
        r_pos = PositionList(ACCT_ID); api.request(r_pos)
        open_positions = r_pos.response.get('positions', [])
        
        for pair in PAIRS:
            r_c = InstrumentsCandles(instrument=pair, params={"count": 150, "granularity": "M5"}); api.request(r_c)
            df = pd.DataFrame([{'close': float(c['mid']['c']), 'high': float(c['mid']['h']), 'low': float(c['mid']['l']), 'volume': float(c['volume']), 'open': float(c['mid']['o'])} for c in r_c.response['candles']])
            stats = get_indicators(df)
            pos = next((p for p in open_positions if p['instrument'] == pair), None)
            
            if pos:
                # SAFE ACCESS for units and price
                lu, su = float(pos.get('long', {}).get('units', 0)), float(pos.get('short', {}).get('units', 0))
                units, side = (lu if lu != 0 else su), (1 if lu != 0 else -1)
                avg_p = pos.get('long', {}).get('averagePrice') or pos.get('short', {}).get('averagePrice')
                
                if units != 0 and avg_p:
                    avg_p = float(avg_p)
                    # 1. Staircase Partial
                    should_scale, s_qty = staircase.process(pair, df['close'].iloc[-1], avg_p, side, units)
                    if should_scale and s_qty > 0:
                        api.request(OrderCreate(ACCT_ID, data={"order": {"units": str(int(-s_qty * side)), "instrument": pair, "type": "MARKET"}}))
                        log.info(f"🚀 SCALE: {pair} | Units: {s_qty}")
                
                    # 2. Virtual Stop (Range Breakout)
                    if stats['adx'] < 25:
                        if (side == 1 and df['close'].iloc[-1] < stats['lower_bb']) or (side == -1 and df['close'].iloc[-1] > stats['upper_bb']):
                            api.request(PositionClose(ACCT_ID, instrument=pair, data={"longUnits": "ALL"} if side == 1 else {"shortUnits": "ALL"}))
                            log.warning(f"🛡️ SHIELD: {pair} Breakout Exit!")
            else:
                side = 0
                if stats['adx'] > 25 and stats['obv_val'] > stats['obv_ma']: # PREDATOR
                    elephant = abs(df['close'].iloc[-1] - df['open'].iloc[-1]) > (stats['atr'] * 2.1)
                    if elephant and df['close'].iloc[-1] > stats['sma150']: 
                        side = 1 if df['close'].iloc[-1] > stats['sma20'] else -1
                elif stats['adx'] < 25: # AIRIBOX
                    if df['close'].iloc[-1] <= stats['lower_bb'] and stats['mfi'] < 30: side = 1
                    elif df['close'].iloc[-1] >= stats['upper_bb'] and stats['mfi'] > 70: side = -1
                
                if side != 0:
                    qty = get_size(balance, stats['atr'])
                    api.request(OrderCreate(ACCT_ID, data={"order": {"units": str(int(qty * side)), "instrument": pair, "type": "MARKET", "timeInForce": "FOK"}}))
                    staircase.active_trades[pair] = {'rung': -1}
                    log.info(f"🎯 FIRE: {pair} | Qty: {qty}")
        time.sleep(60)
    except Exception as e: log.error(f"BEAST ERROR: {e}"); time.sleep(30)
