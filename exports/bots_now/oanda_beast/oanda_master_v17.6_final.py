#!/usr/bin/env python3
"""
🚀 OANDA MASTER v17.6.5 FINAL – STABLE DICTIONARY STATE BUILD
Target: Demo Account REDACTED_OANDA_ACCOUNT
"""

import os, time, math, logging, datetime
import pandas as pd
import numpy as np
import v20
from river import linear_model, preprocessing, optim, metrics

OANDA_URL = "api-fxpractice.oanda.com"
OANDA_TOKEN = "REDACTED"
ACCOUNT_ID = "REDACTED_OANDA_ACCOUNT"

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
log = logging.getLogger(__name__)

class Config:
    STRIKE_BP = 0.20
    DAILY_DD_LIMIT = 0.0075
    MAX_SPREAD_PIPS = 2.0
    PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]

# --- STATE DICTIONARY (Bypasses Global SyntaxErrors) ---
STATE = {
    'daily_start_equity': None,
    'start_equity': None,
    'virtual_stops': {},
    'trade_history': []
}

ctx = v20.Context(OANDA_URL, 443, token=OANDA_TOKEN)
mae_metric = metrics.MAE()

def get_market_info(pair):
    try:
        res = ctx.pricing.get(ACCOUNT_ID, instruments=pair)
        p = res.get("prices")[0]
        ask, bid = float(p.closeoutAsk), float(p.closeoutBid)
        pip_val = 0.01 if "JPY" in pair else 0.0001
        return ask, bid, pip_val, (ask - bid) / pip_val
    except: return 0, 0, 0, 99

def manage_exits():
    try:
        res = ctx.position.list_open(ACCOUNT_ID)
        for pos in res.get("positions"):
            pair = pos.instrument
            units = float(pos.long.units) if float(pos.long.units) != 0 else float(pos.short.units)
            if units == 0: continue
            side = "LONG" if units > 0 else "SHORT"
            entry = float(pos.long.averagePrice if side == "LONG" else pos.short.averagePrice)
            ask, bid, pip_val, _ = get_market_info(pair)
            curr = bid if side == "LONG" else ask
            pips = (curr - entry) / pip_val if side == "LONG" else (entry - curr) / pip_val
            
            if pair not in STATE['virtual_stops']:
                STATE['virtual_stops'][pair] = {'entry': entry, 'trail': entry, 'staged': False}
            
            st = STATE['virtual_stops'][pair]
            if pips >= 15 and not st['staged']:
                half = str(int(abs(units)/2))
                ctx.position.close(ACCOUNT_ID, pair, longUnits=half if side == "LONG" else None, shortUnits=half if side == "SHORT" else None)
                log.info(f"💰 BANKED 50% on {pair} (+15 Pips)")
                st['staged'] = True; st['trail'] = entry; continue

            if (side == "LONG" and curr < st['trail']) or (side == "SHORT" and curr > st['trail']):
                ctx.position.close(ACCOUNT_ID, pair, longUnits="ALL" if side == "LONG" else None, shortUnits="ALL" if side == "SHORT" else None)
                log.info(f"🛑 EXIT: {pair} Trail Hit"); STATE['virtual_stops'].pop(pair, None)
    except: pass

log.info("🏛️ OANDA MASTER v17.6.5 – ARMED & STABLE")

while True:
    try:
        now = datetime.datetime.now()
        acct = ctx.account.summary(ACCOUNT_ID).get("account")
        equity = float(acct.balance)
        
        if STATE['start_equity'] is None: STATE['start_equity'] = equity
        if STATE['daily_start_equity'] is None or now.hour == 0: 
            STATE['daily_start_equity'] = equity

        day_loss = (STATE['daily_start_equity'] - equity) / STATE['daily_start_equity']

        if day_loss >= Config.DAILY_DD_LIMIT:
            log.warning("⚠️ DAILY LIMIT HIT"); time.sleep(3600); continue
            
        manage_exits()

        for pair in Config.PAIRS:
            ask, bid, pip_val, spread = get_market_info(pair)
            if spread > Config.MAX_SPREAD_PIPS: continue
            
            c_res = ctx.instrument.candles(pair, count=50, granularity="M1")
            df = pd.DataFrame([{'h':float(c.mid.h),'l':float(c.mid.l),'c':float(c.mid.c),'o':float(c.mid.o)} for c in c_res.get("candles") if c.complete])
            
            if not df.empty:
                body = (df['c'] - df['o']).abs()
                if body.iloc[-1] > body.rolling(20).mean().iloc[-1] * 2.1:
                    units = int((equity * Config.STRIKE_BP) / df['c'].iloc[-1])
                    dir_u = units if df['c'].iloc[-1] > df['o'].iloc[-1] else -units
                    ctx.order.market(ACCOUNT_ID, instrument=pair, units=dir_u)
                    log.info(f"🔥 STRIKE: {pair} | Units: {abs(dir_u)}")
                    STATE['trade_history'].append(pair)

        mae_metric.update(equity, equity)
        roi = ((equity - STATE['start_equity']) / STATE['start_equity']) * 100
        log.info(f"📊 Equity ${equity:,.2f} | Daily Loss {day_loss*100:.2f}% | ROI {roi:.2f}% | MAE {mae_metric.get():.4f}")
        time.sleep(60)
    except Exception as e:
        log.error(f"ENGINE ERROR: {e}"); time.sleep(10)
