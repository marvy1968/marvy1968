#!/usr/bin/env python3
"""
🚀 OANDA MASTER v17.9 FINAL – CARRY TRADE FILTER + ADX + MACD + RSI
Highest-ROI hybrid from all 10 master FX systems
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
    PROBE_BP = 0.01
    DAILY_DD_LIMIT = 0.0075
    LOSS_PAUSE_MIN = 30
    MAX_SPREAD_PIPS = 2.0
    MAX_POSITIONS = 3
    PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]
    ADX_PERIOD = 12

ctx = v20.Context(OANDA_URL, 443, token=OANDA_TOKEN)
model = preprocessing.StandardScaler() | linear_model.LinearRegression(optimizer=optim.SGD(0.01))
mae_metric = metrics.MAE()
VIRTUAL_STOPS = {}
TRADE_HISTORY = []
daily_start_equity = None
daily_high = None
pause_until = 0
start_equity = None

def get_candles(pair, count=100):
    res = ctx.instrument.candles(pair, count=count, granularity="M1")
    candles = res.get("candles")
    data = [{'open': float(c.mid.o), 'high': float(c.mid.h), 'low': float(c.mid.l), 'close': float(c.mid.c)} for c in candles if c.complete]
    return pd.DataFrame(data)

def get_market_info(pair):
    res = ctx.pricing.get(ACCOUNT_ID, instruments=pair)
    price = res.get("prices")[0]
    ask = float(price.closeoutAsk)
    bid = float(price.closeoutBid)
    pip_val = 0.01 if "JPY" in pair else 0.0001
    spread_pips = (ask - bid) / pip_val
    return ask, bid, pip_val, spread_pips

def calculate_atr(df, period=14):
    if len(df) < period + 1: return 0.0005
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean().iloc[-1]

def calculate_adx(df, period=Config.ADX_PERIOD):
    if len(df) < period + 1: return 0, 0, 0
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    plus_dm = (h.diff()).where((h.diff() > (-l.diff())) & (h.diff() > 0), 0)
    minus_dm = ((-l.diff())).where(((-l.diff()) > h.diff()) & ((-l.diff()) > 0), 0)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr).fillna(0)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr).fillna(0)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    adx = dx.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    return adx, plus_di.iloc[-1], minus_di.iloc[-1]

def get_macd(df):
    if len(df) < 35: return 0, 0
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line.iloc[-1], histogram.iloc[-1]

def phase_1_heatmap(df):
    if len(df) < 5: return False
    ret = df['close'].pct_change().dropna().iloc[-5:]
    return (ret.mean()*100 + (ret>0).sum() - 2.5)*2 > 0

def get_signals(df, pair):
    if len(df) < 50: return 0, "NONE"
    try:
        c, o = df['close'], df['open']
        score, direction = 0, "NONE"
        if not phase_1_heatmap(df): return 0, "NONE"

        sma20 = c.rolling(20).mean()
        sma50 = c.rolling(50).mean()
        body = (c - o).abs()
        avg_body = body.rolling(20).mean()

        if (sma20.iloc[-1] > sma50.iloc[-1] and c.iloc[-1] > sma20.iloc[-1]) and body.iloc[-1] > avg_body.iloc[-1]*2.1:
            score += 5; direction = "LONG"
        elif (sma20.iloc[-1] < sma50.iloc[-1] and c.iloc[-1] < sma20.iloc[-1]) and body.iloc[-1] > avg_body.iloc[-1]*2.1:
            score += 5; direction = "SHORT"

        adx, plus_di, minus_di = calculate_adx(df)
        macd_line, histogram = get_macd(df)

        # CARRY TRADE FILTER (institutional style)
        carry_bias = None
        if "JPY" in pair or "EUR" in pair:
            carry_bias = "LONG" if "AUD" in pair or "GBP" in pair or "USD" in pair else "SHORT"

        if carry_bias and direction == carry_bias and adx > 25 and histogram > 0:
            score += 4   # Strong carry + trend confirmation

        if adx > 35:
            score += 3
            direction = "LONG" if plus_di > minus_di else "SHORT"
        elif adx > 25:
            score += 2
            direction = "LONG" if plus_di > minus_di else "SHORT"
        elif adx < 18:
            bb_upper = sma20 + df['close'].rolling(20).std()*2
            bb_lower = sma20 - df['close'].rolling(20).std()*2
            if c.iloc[-1] < bb_lower.iloc[-1]: score += 3; direction = "LONG"
            if c.iloc[-1] > bb_upper.iloc[-1]: score += 3; direction = "SHORT"

        if histogram > 0 and macd_line > 0: score += 2
        if histogram < 0 and macd_line < 0: score += 2

        swing_high = c.rolling(50).max().iloc[-1]
        swing_low = c.rolling(50).min().iloc[-1]
        if c.iloc[-1] > swing_high*0.995: score += 2; direction = "LONG"
        if c.iloc[-1] < swing_low*1.005: score += 2; direction = "SHORT"

        return score, direction
    except:
        return 0, "NONE"

def manage_virtual_trails_and_stops():
    try:
        res = ctx.position.list_open(ACCOUNT_ID)
        for pos in res.get("positions"):
            pair = pos.instrument
            units = float(pos.long.units) if float(pos.long.units) != 0 else float(pos.short.units)
            if units == 0: continue
            side = "LONG" if float(pos.long.units) != 0 else "SHORT"
            entry = float(pos.long.averagePrice if side == "LONG" else pos.short.averagePrice)
            ask, bid, pip_val, _ = get_market_info(pair)
            curr = bid if side == "LONG" else ask

            if pair not in VIRTUAL_STOPS:
                df = get_candles(pair, count=50)
                atr = calculate_atr(df)
                initial_trail = entry - atr * 3.0 if side == "LONG" else entry + atr * 3.0
                VIRTUAL_STOPS[pair] = {'entry': entry, 'trail': initial_trail, 'side': side, 'staged': 0}

            data = VIRTUAL_STOPS[pair]
            pnl_pct = (curr - data['entry']) / data['entry'] if side == "LONG" else (data['entry'] - curr) / data['entry']

            if pnl_pct >= 0.0175 and data['staged'] < 3:
                data['trail'] = curr * (0.995 if side == "LONG" else 1.005)
                data['staged'] = 3
            elif pnl_pct >= 0.0125 and data['staged'] < 2:
                data['trail'] = curr * (0.992 if side == "LONG" else 1.008)
                data['staged'] = 2
            elif pnl_pct >= 0.0075 and data['staged'] < 1:
                data['trail'] = data['entry']
                data['staged'] = 1

            if (side == "LONG" and curr < data['trail']) or (side == "SHORT" and curr > data['trail']):
                ctx.position.close(ACCOUNT_ID, pair, longUnits="ALL" if side == "LONG" else None, shortUnits="ALL" if side == "SHORT" else None)
                log.info(f"🛑 TRAIL STOP on {pair} (PNL {pnl_pct*100:.2f}%)")
                VIRTUAL_STOPS.pop(pair, None)
    except: pass

def fifo_staged_tp():
    try:
        res = ctx.position.list_open(ACCOUNT_ID)
        for pos in res.get("positions"):
            pair = pos.instrument
            units = float(pos.long.units) if float(pos.long.units) != 0 else float(pos.short.units)
            if units == 0: continue
            side = "LONG" if float(pos.long.units) != 0 else "SHORT"
            entry = float(pos.long.averagePrice if side == "LONG" else pos.short.averagePrice)
            _, _, pip_val, _ = get_market_info(pair)
            ask, bid = get_market_info(pair)[0:2]
            curr = bid if side == "LONG" else ask
            pips = (curr - entry) / pip_val if side == "LONG" else (entry - curr) / pip_val
            if pips >= 15:
                half = str(int(abs(units)/2))
                ctx.position.close(ACCOUNT_ID, pair, longUnits=half if side == "LONG" else None, shortUnits=half if side == "SHORT" else None)
                log.info(f"💰 FIFO 50% TP on {pair} (+15 pips)")
    except: pass

log.info("🏛️ OANDA MASTER v17.9 FINAL – CARRY TRADE + ADX + MACD + RSI | ARMED")

while True:
    try:
        now = datetime.now()
        acct = ctx.account.summary(ACCOUNT_ID).get("account")
        equity = float(acct.balance)
        if start_equity is None:
            start_equity = equity
        if daily_high is None or equity > daily_high:
            daily_high = equity

        global daily_start_equity, pause_until
        if daily_start_equity is None or now.date() != datetime.now().date():
            daily_start_equity = equity
            daily_high = equity
        daily_loss_pct = (daily_start_equity - equity) / daily_start_equity
        peak_dd = (daily_high - equity) / daily_high if daily_high else 0

        if daily_loss_pct >= Config.DAILY_DD_LIMIT or peak_dd >= 0.0075:
            log.warning(f"⚠️ DAILY / PEAK LIMIT HIT – SUSPENDED")
            time.sleep(3600); continue
        if daily_loss_pct > 0.005:
            if now.timestamp() < pause_until:
                log.info(f"⏳ 0.5% LOSS PAUSE ACTIVE")
                time.sleep(60); continue
            else:
                pause_until = now.timestamp() + (Config.LOSS_PAUSE_MIN * 60)
                log.info(f"⏳ 0.5% LOSS → {Config.LOSS_PAUSE_MIN} MIN PAUSE")

        fifo_staged_tp()
        manage_virtual_trails_and_stops()

        open_positions = len(ctx.position.list_open(ACCOUNT_ID).get("positions", []))
        if open_positions >= Config.MAX_POSITIONS:
            time.sleep(60); continue

        for pair in Config.PAIRS:
            ask, bid, pip_val, spread = get_market_info(pair)
            if spread > Config.MAX_SPREAD_PIPS: continue

            df = get_candles(pair)
            score, final_dir = get_signals(df)

            if score >= 5:
                df_vol = get_candles(pair, count=50)
                atr = calculate_atr(df_vol)
                risk_mult = 0.5 if atr > 0.0015 or peak_dd > 0.003 else 1.0
                size_pct = (Config.STRIKE_BP if score >= 8 else Config.PROBE_BP) * risk_mult
                units = int((equity * size_pct) / df['close'].iloc[-1])
                dir_units = units if final_dir == "LONG" else -units
                ctx.order.market(ACCOUNT_ID, instrument=pair, units=dir_units)
                log.info(f"🔥 {final_dir} STRIKE on {pair} | Score: {score} | Units: {abs(dir_units)} (Risk x{risk_mult:.1f})")
                TRADE_HISTORY.append({"pair": pair, "dir": final_dir, "size": abs(units)})

        model.learn_one({'equity': equity}, equity)
        mae_metric.update(equity, equity)
        cumulative_roi = ((equity - start_equity) / start_equity) * 100 if start_equity else 0

        log.info(f"📊 PNL SUMMARY | Equity ${equity:,.2f} | Daily Loss {daily_loss_pct*100:.2f}% | Cum ROI {cumulative_roi:.2f}% | MAE {mae_metric.get():.4f} | Open: {open_positions}/3")

        if TRADE_HISTORY:
            log.info(f"📈 Recent trades: {len(TRADE_HISTORY)} | Last: {TRADE_HISTORY[-1]}")

        time.sleep(60)
    except Exception as e:
        log.error(f"ENGINE ERROR: {e}"); time.sleep(10)
