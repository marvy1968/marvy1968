#!/usr/bin/env python3
import os
import sys
import time
import json
import logging
import datetime
import requests
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("/home/moonrider1968/bots/killer_beast_sniper/oanda_daemon.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Load credentials from existing environment or fallback to active defaults
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxtrade.oanda.com")

# Fallback extraction from backup if env is unset
if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    try:
        with open("oanda_stage4_daemon.py.bak_pre_m3_flat", "r") as f:
            for line in f:
                if line.startswith("OANDA_API_KEY") and "=" in line and not OANDA_API_KEY:
                    OANDA_API_KEY = line.split("=")[1].strip().strip('"').strip("'")
                elif line.startswith("OANDA_ACCOUNT_ID") and "=" in line and not OANDA_ACCOUNT_ID:
                    OANDA_ACCOUNT_ID = line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

VIRTUAL_STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/virtual_ratchet_state.json"

RISK_PCT_NAV = 0.005          # 0.50% risk per trade
MAX_GROSS_LEVERAGE = 2.0      # 2.0x gross NAV notional ceiling
MIN_UNITS = 1000

PAIR_CONFIG = {
    "USD_JPY": {"max_spread": 2.5, "unit_cap": 20000},
    "USD_CAD": {"max_spread": 2.5, "unit_cap": 35000}
}

daily_starting_equity = None
daily_high_water_equity = None
current_trading_day = None
cooldown_until = datetime.datetime.min
lockdown_active = False


def calculate_position_units(sym: str, current_price: float, stop_dist_quote: float, nav: float) -> int:
    if nav <= 0 or stop_dist_quote <= 0 or current_price <= 0:
        return 0

    risk_dollars = nav * RISK_PCT_NAV
    base, quote = sym.split('_')

    if quote == 'USD':
        units_stop = risk_dollars / stop_dist_quote
        notional_max = (nav * MAX_GROSS_LEVERAGE) / current_price
    elif base == 'USD':
        units_stop = (risk_dollars * current_price) / stop_dist_quote
        notional_max = nav * MAX_GROSS_LEVERAGE
    else:
        return 0

    safe_units = min(units_stop, notional_max)
    cap = PAIR_CONFIG.get(sym, {}).get("unit_cap", 20000)
    safe_units = min(safe_units, cap)

    units = int(safe_units)
    return units if units >= MIN_UNITS else 0


def get_account_summary():
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            acc = r.json().get('account', {})
            return float(acc.get('balance', 0.0)), float(acc.get('NAV', 0.0)), int(acc.get('openPositionCount', 0))
    except Exception as e:
        logging.error(f"API Error get_account_summary: {e}")
    return 0.0, 0.0, 0


def get_open_trades():
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get('trades', [])
    except Exception as e:
        logging.error(f"API Error get_open_trades: {e}")
    return []


def get_current_spread(sym):
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={sym}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            prices = r.json().get('prices', [])
            if prices:
                ask = float(prices[0]['asks'][0]['price'])
                bid = float(prices[0]['bids'][0]['price'])
                mult = 100.0 if "JPY" in sym else 10000.0
                return (ask - bid) * mult, ask, bid
    except Exception as e:
        logging.error(f"API Error get_current_spread: {e}")
    return 999.0, 0.0, 0.0


def get_candles(sym, count=60):
    url = f"{OANDA_URL}/v3/instruments/{sym}/candles?granularity=H1&count={count}&price=M"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = []
            for c in r.json().get('candles', []):
                if c.get('complete'):
                    data.append({
                        "time": c['time'],
                        "open": float(c['mid']['o']),
                        "high": float(c['mid']['h']),
                        "low": float(c['mid']['l']),
                        "close": float(c['mid']['c'])
                    })
            return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"API Error get_candles: {e}")
    return None


def execute_order(sym, units, side, disaster_sl_px):
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders"
    payload = {
        "order": {
            "instrument": sym,
            "units": str(units if side == 'BUY' else -units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "price": f"{disaster_sl_px:.5f}" if "JPY" not in sym else f"{disaster_sl_px:.3f}",
                "timeInForce": "GTC"
            }
        }
    }
    try:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=10)
        res = r.json()
        if "orderFillTransaction" in res:
            fill = res["orderFillTransaction"]
            trade_id = fill.get("tradeOpened", {}).get("tradeID") or fill.get("id")
            fill_px = float(fill.get("price"))
            logging.info(f"✅ [ORDER FILLED] {side} {units} {sym} @ {fill_px} (SL: {disaster_sl_px:.4f})")
            return trade_id, fill_px
        else:
            logging.error(f"❌ Order Rejected: {res}")
    except Exception as e:
        logging.error(f"Execute order failed: {e}")
    return None, None


def close_trade(trade_id, units_to_close=None):
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close"
    payload = {"units": "ALL"} if units_to_close is None else {"units": str(units_to_close)}
    try:
        r = requests.put(url, headers=HEADERS, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logging.error(f"Close trade failed for {trade_id}: {e}")
    return False


def load_virtual_state():
    if os.path.exists(VIRTUAL_STATE_FILE):
        try:
            with open(VIRTUAL_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_virtual_state(st):
    try:
        with open(VIRTUAL_STATE_FILE, "w") as f:
            json.dump(st, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save virtual state: {e}")


def compute_indicators(df, sym):
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

    up_move = df['high'] - df['high'].shift(1)
    down_move = df['low'].shift(1) - df['low']
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    smooth_pos = pd.Series(pos_dm).rolling(14).mean()
    smooth_neg = pd.Series(neg_dm).rolling(14).mean()
    di_plus = 100.0 * (smooth_pos / (df['atr'] + 1e-9))
    di_minus = 100.0 * (smooth_neg / (df['atr'] + 1e-9))
    dx = 100.0 * (abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9))
    df['adx'] = dx.rolling(14).mean()

    return df


def run_trading_cycle():
    global daily_starting_equity, daily_high_water_equity, current_trading_day, cooldown_until, lockdown_active

    now = datetime.datetime.utcnow()
    today_str = now.strftime('%Y-%m-%d')

    balance, nav, open_pos_count = get_account_summary()
    if nav <= 0:
        return

    logging.info(f"💓 Scan Cycle | NAV: ${nav:,.2f} | Open Pos: {open_pos_count}")

    if current_trading_day != today_str:
        current_trading_day = today_str
        daily_starting_equity = nav
        daily_high_water_equity = nav
        lockdown_active = False

    daily_high_water_equity = max(daily_high_water_equity, nav)
    daily_dd_pct = ((nav - daily_high_water_equity) / daily_high_water_equity) * 100.0

    if daily_dd_pct <= -1.00:
        if not lockdown_active:
            logging.critical(f"🛑 [TIER 3] NAV Drawdown {daily_dd_pct:.2f}%. Entering 8h Lockdown.")
            cooldown_until = now + datetime.timedelta(hours=8)
            lockdown_active = True
    elif daily_dd_pct <= -0.25:
        if now >= cooldown_until:
            logging.warning(f"⚠️ [TIER 2] NAV Drawdown {daily_dd_pct:.2f}%. 30-Min Entry Freeze.")
            cooldown_until = now + datetime.timedelta(minutes=30)

    # 1. Open Trade Management
    virtual_state = load_virtual_state()
    open_trades = get_open_trades()
    active_ids = {str(t['id']) for t in open_trades}

    for st_id in list(virtual_state.keys()):
        if st_id not in active_ids:
            logging.info(f"Trade {st_id} closed externally/disaster SL. Clearing state.")
            del virtual_state[st_id]

    for t in open_trades:
        tid = str(t['id'])
        sym = t['instrument']
        units = int(t['currentUnits'])
        entry_px = float(t['price'])

        _, ask, bid = get_current_spread(sym)
        current_px = bid if units > 0 else ask

        if tid not in virtual_state:
            pip_val = 0.01 if "JPY" in sym else 0.0001
            virtual_state[tid] = {
                "instrument": sym,
                "regime": "TREND" if "JPY" in sym else "MEAN_REV",
                "remaining_units": units,
                "entry_px": entry_px,
                "initial_risk": 20.0 * pip_val,
                "virtual_sl": entry_px - (20.0 * pip_val) if units > 0 else entry_px + (20.0 * pip_val),
                "tp_tier": 0
            }

        v = virtual_state[tid]
        initial_risk = v["initial_risk"]
        r_mult = ((current_px - entry_px) / initial_risk) if units > 0 else ((entry_px - current_px) / initial_risk)

        if (units > 0 and current_px <= v["virtual_sl"]) or (units < 0 and current_px >= v["virtual_sl"]):
            logging.info(f"🛑 [VIRTUAL SL] {sym} Trade {tid} hit SL @ {current_px:.4f}. Closing.")
            if close_trade(tid):
                del virtual_state[tid]
                continue

        if v.get("regime") == "MEAN_REV" and v["tp_tier"] < 1:
            if r_mult >= 1.0:
                v["virtual_sl"] = entry_px
                v["tp_tier"] = 1
                logging.info(f"🎯 [MR MIDLINE] {sym} Trade {tid} reached 1.0R. Locked BE.")

        if v.get("regime") == "TREND":
            if r_mult >= 1.2 and v["tp_tier"] < 1:
                v["virtual_sl"] = entry_px + (0.2 * initial_risk) if units > 0 else entry_px - (0.2 * initial_risk)
                v["tp_tier"] = 1
                logging.info(f"🛡️ [TREND BE] {sym} Trade {tid} reached +1.2R. Locked BE+0.2R.")

            if r_mult >= 2.0 and v["tp_tier"] < 2:
                harvest = int(v["remaining_units"] * 0.5)
                if harvest >= MIN_UNITS:
                    logging.info(f"💰 [TREND HARVEST] {sym} Trade {tid} reached +2.0R. Harvesting 50% ({harvest} units).")
                    if close_trade(tid, units_to_close=harvest):
                        v["remaining_units"] -= harvest
                        v["virtual_sl"] = entry_px + (1.0 * initial_risk) if units > 0 else entry_px - (1.0 * initial_risk)
                        v["tp_tier"] = 2

    save_virtual_state(virtual_state)

    # 2. Entry Gates
    if now < cooldown_until:
        rem_min = (cooldown_until - now).total_seconds() / 60.0
        logging.info(f"⏳ Cooldown Active ({rem_min:.1f}m remaining). Skipping new entries.")
        return

    active_symbols = {t['instrument'] for t in open_trades}

    for sym, cfg in PAIR_CONFIG.items():
        if sym in active_symbols:
            continue

        spread_pips, ask, bid = get_current_spread(sym)
        if spread_pips > cfg["max_spread"]:
            continue

        df = get_candles(sym, count=60)
        if df is None or len(df) < 55:
            continue

        df = compute_indicators(df, sym)
        row = df.iloc[-1]
        prev = df.iloc[-2]
        pip_val = 0.01 if "JPY" in sym else 0.0001

        current_adx = row['adx']
        regime = "TREND" if current_adx >= 22.0 else "MEAN_REV"

        stop_dist = max(15.0 * pip_val, 1.5 * row['atr'])
        disaster_stop_dist = 3.0 * row['atr']
        units = calculate_position_units(sym, float(row['close']), stop_dist, nav)

        if units < MIN_UNITS:
            continue

        trade_id, fill_px = None, None

        if regime == "MEAN_REV":
            if (prev['low'] <= prev['bb_lower']) and (row['close'] > row['bb_lower']) and (row['rsi'] < 38):
                disaster_sl = row['close'] - disaster_stop_dist
                trade_id, fill_px = execute_order(sym, units, 'BUY', disaster_sl)
            elif (prev['high'] >= prev['bb_upper']) and (row['close'] < row['bb_upper']) and (row['rsi'] > 62):
                disaster_sl = row['close'] + disaster_stop_dist
                trade_id, fill_px = execute_order(sym, units, 'SELL', disaster_sl)

        elif regime == "TREND":
            if (row['close'] > row['ema21']) and (prev['low'] <= row['ema21']) and (row['close'] > prev['high']):
                disaster_sl = row['close'] - disaster_stop_dist
                trade_id, fill_px = execute_order(sym, units, 'BUY', disaster_sl)
            elif (row['close'] < row['ema21']) and (prev['high'] >= row['ema21']) and (row['close'] < prev['low']):
                disaster_sl = row['close'] + disaster_stop_dist
                trade_id, fill_px = execute_order(sym, units, 'SELL', disaster_sl)

        if trade_id:
            virtual_state[str(trade_id)] = {
                "instrument": sym,
                "regime": regime,
                "remaining_units": units,
                "entry_px": fill_px,
                "initial_risk": stop_dist,
                "virtual_sl": fill_px - stop_dist if units > 0 else fill_px + stop_dist,
                "tp_tier": 0
            }
            save_virtual_state(virtual_state)


if __name__ == "__main__":
    logging.info("🚀 MicroBeast M3 Daemon Initialized.")
    while True:
        try:
            run_trading_cycle()
        except Exception as e:
            logging.error(f"Cycle exception: {e}")
        time.sleep(120)
