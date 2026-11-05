#!/usr/bin/env python3
"""
🚀 OANDA MASTER v17.4.2 FINAL – GLOBAL SCOPE FIX
"""

import os, time, math, logging, datetime
import pandas as pd
import numpy as np
import v20
from river import linear_model, preprocessing, optim, metrics

OANDA_URL = "api-fxpractice.oanda.com"
OANDA_TOKEN = os.environ.get("OANDA_TOKEN", "REDACTED_BROKER_KEY")
ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "45Nxk7wHRq3xJ9eoPfYYcVDQ1MACV4jX4zSv9HAzj8KR")

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
log = logging.getLogger(__name__)

class Config:
    STRIKE_BP = 0.20
    PROBE_BP = 0.01
    DAILY_DD_LIMIT = 0.0075
    LOSS_PAUSE_MIN = 30
    MAX_SPREAD_PIPS = 2.0
    PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]

ctx = v20.Context(OANDA_URL, 443, token=OANDA_TOKEN)
model = preprocessing.StandardScaler() | linear_model.LinearRegression(optimizer=optim.SGD(0.01))
mae_metric = metrics.MAE()
VIRTUAL_STOPS = {}
TRADE_HISTORY = []
daily_start_equity = None
pause_until = 0
start_equity = None

def get_candles(pair, count=100):
    try:
        res = ctx.instrument.candles(pair, count=count, granularity="M1")
        data = [{'open': float(c.mid.o), 'high': float(c.mid.h), 'low': float(c.mid.l), 'close': float(c.mid.c)} for c in res.get("candles") if c.complete]
        return pd.DataFrame(data)
    except: return pd.DataFrame()

def get_market_info(pair):
    try:
        res = ctx.pricing.get(ACCOUNT_ID, instruments=pair)
        p = res.get("prices")[0]
        ask, bid = float(p.closeoutAsk), float(p.closeoutBid)
        pip_val = 0.01 if "JPY" in pair else 0.0001
        return ask, bid, pip_val, (ask - bid) / pip_val
    except: return 0, 0, 0, 99

def calculate_adx(df, period=14):
    if len(df) < period + 1: return 0
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    plus_dm = (h.diff()).where((h.diff() > (-l.diff())) & (h.diff() > 0), 0)
    minus_dm = ((-l.diff())).where(((-l.diff()) > h.diff()) & ((-l.diff()) > 0), 0)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr).fillna(0)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr).fillna(0)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    return dx.ewm(alpha=1/period, adjust=False).mean().iloc[-1]

def manage_virtual_trails_and_stops():
    try:
        res = ctx.position.list_open(ACCOUNT_ID)
        for pos in res.get("positions"):
            pair = pos.instrument
            units = float(pos.long.units) if float(pos.long.units) != 0 else float(pos.short.units)
            if units == 0: continue
            side = "LONG" if units > 0 else "SHORT"
            entry = float(pos.long.averagePrice if side == "LONG" else pos.short.averagePrice)
            ask, bid, _, _ = get_market_info(pair)
            curr = bid if side == "LONG" else ask
            if pair not in VIRTUAL_STOPS: VIRTUAL_STOPS[pair] = {'entry': entry, 'trail': entry, 'staged': 0}
            data = VIRTUAL_STOPS[pair]
            pnl = (curr - data['entry']) / data['entry'] if side == "LONG" else (data['entry'] - curr) / data['entry']
            if pnl >= 0.015 and data['staged'] < 3: data['trail'] = curr * (0.995 if side == "LONG" else 1.005); data['staged'] = 3
            elif pnl >= 0.010 and data['staged'] < 2: data['trail'] = curr * (0.992 if side == "LONG" else 1.008); data['staged'] = 2
            elif pnl >= 0.005 and data['staged'] < 1: data['trail'] = curr * (0.990 if side == "LONG" else 1.010); data['staged'] = 1
            if (side == "LONG" and curr < data['trail']) or (side == "SHORT" and curr > data['trail']):
                ctx.position.close(ACCOUNT_ID, pair, longUnits="ALL" if side == "LONG" else None, shortUnits="ALL" if side == "SHORT" else None)
                log.info(f"🛑 TRAIL STOP on {pair}"); VIRTUAL_STOPS.pop(pair, None)
    except: pass

log.info("🏛️ OANDA MASTER v17.4.2 – ARMED")

while True:
    # GLOBAL DECLARATION FIRST
    global daily_start_equity, pause_until, start_equity
    
    try:
        now = datetime.now()
        acct_res = ctx.account.summary(ACCOUNT_ID)
        acct = acct_res.get("account")
        equity = float(acct.balance)
        
        if start_equity is None: start_equity = equity
        if daily_start_equity is None or now.hour == 0: daily_start_equity = equity
        
        daily_loss_pct = (daily_start_equity - equity) / daily_start_equity

        if daily_loss_pct >= Config.DAILY_DD_LIMIT:
            log.warning(f"⚠️ DAILY LIMIT HIT"); time.sleep(3600); continue
            
        manage_virtual_trails_and_stops()

        for pair in Config.PAIRS:
            ask, bid, pip_val, spread = get_market_info(pair)
            if spread > Config.MAX_SPREAD_PIPS: continue
            df = get_candles(pair)
            if not df.empty:
                c, o = df['close'], df['open']
                ret = c.pct_change().dropna().iloc[-5:]
                if (ret.mean()*100 + (ret>0).sum() - 2.5)*2 > 0:
                    if (c.iloc[-1] - o.iloc[-1]).abs() > (c - o).abs().rolling(20).mean().iloc[-1] * 2.1:
                        if calculate_adx(df) > 20:
                            units = int((equity * Config.STRIKE_BP) / c.iloc[-1])
                            dir_units = units if c.iloc[-1] > o.iloc[-1] else -units
                            ctx.order.market(ACCOUNT_ID, instrument=pair, units=dir_units)
                            log.info(f"🔥 STRIKE on {pair} | Units: {abs(dir_units)}")
                            TRADE_HISTORY.append({"pair": pair, "time": now.strftime("%H:%M")})

        log.info(f"📊 Equity ${equity:,.2f} | Loss {daily_loss_pct*100:.2f}% | ROI {((equity-start_equity)/start_equity)*100:.2f}%")
        time.sleep(60)
    except Exception as e:
        log.error(f"ENGINE ERROR: {e}"); time.sleep(10)
