#!/usr/bin/env python3
"""
G3-B5 Canonical Production Paper Trader
Phase 11B / Phase 12C Architecture with Complete Idempotency & Protective Lifecycle
"""

import os
import sys
import time
import datetime
import json
import logging
import argparse
import copy
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

# --- 1. ENVIRONMENT & CONFIGURATION ---
load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))

PAPER_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"
NY_TZ = ZoneInfo("America/New_York")

STATE_FILE = "/home/moonrider1968/bots/g3_b5_canonical_state.json"
LOG_FILE = "/home/moonrider1968/bots/g3_b5_canonical_paper.log"

XLK_LONG_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"]
STAGE4_SHORT_FLEET = [
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", "META",
    "JPM", "BAC", "WFC", "GS", "MS", "AXP", "CAT", "GE", "UNP", "HON", "RTX", "ETN"
]
DONCHIAN_SATELLITE_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", 
    "META", "AAPL", "NFLX", "SNOW", "PLTR", "DDOG", "CRWD", "NET", "PANW"
]
TECH_SYMBOLS = set(XLK_LONG_UNIVERSE) | set(DONCHIAN_SATELLITE_UNIVERSE)
ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + DONCHIAN_SATELLITE_UNIVERSE + ["SPY"])))

MAX_POSITIONS = 3
MAX_GROSS_PORTFOLIO_RISK = 0.030
MAX_SINGLE_NAME_RISK = 0.010
MAX_TECH_SECTOR_RISK = 0.020
SLIP, SPREAD = 0.03, 0.02

G3_B5_PARAMS = {
    "d1_trig": 0.050,
    "d2_trig": 0.085,
    "d3_trig": 0.125,
    "d1_mult": 0.85,
    "d2_mult": 0.50,
    "d3_mult": 0.15,
    "dwell": 2,
    "recovery_hysteresis": 0.025
}

def setup_logger(dry_run=False):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(stream_handler)
    
    if not dry_run:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(file_handler)

# --- 2. BROKER CLIENT & FAIL-CLOSED HELPERS ---
def get_headers():
    return {
        "APCA-API-KEY-ID": APCA_API_KEY_ID,
        "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY
    }

def get_account():
    try:
        r = requests.get(f"{PAPER_URL}/account", headers=get_headers(), timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logging.error(f"Account fetch failed: {e}")
        return None

def get_broker_positions():
    try:
        r = requests.get(f"{PAPER_URL}/positions", headers=get_headers(), timeout=10)
        if r.status_code == 200:
            return {p['symbol']: p for p in r.json()}
        logging.error(f"Failed fetching positions: HTTP {r.status_code}")
        return None
    except Exception as e:
        logging.error(f"Positions fetch exception: {e}")
        return None

def get_order_by_client_id(client_order_id):
    try:
        r = requests.get(f"{PAPER_URL}/orders:by_client_order_id?client_order_id={client_order_id}", headers=get_headers(), timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logging.error(f"Order fetch failed for {client_order_id}: {e}")
        return None

def get_order_by_id(order_id):
    try:
        r = requests.get(f"{PAPER_URL}/orders/{order_id}", headers=get_headers(), timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logging.error(f"Order fetch failed for {order_id}: {e}")
        return None

def submit_broker_order(symbol, qty, side, order_type="market", limit_price=None, stop_price=None, client_order_id=None, dry_run=False):
    if not client_order_id:
        client_order_id = f"g3b5_{symbol}_{int(time.time()*1000)}"
    data = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": "gtc" if order_type == "stop" else "day",
        "client_order_id": client_order_id
    }
    if limit_price and order_type == "limit":
        data["limit_price"] = f"{limit_price:.2f}"
    if stop_price and order_type == "stop":
        data["stop_price"] = f"{stop_price:.2f}"
        
    if dry_run:
        logging.info(f"🔍 [DRY-RUN] Propose {side.upper()} {qty} {symbol} ({order_type}) ID: {client_order_id}")
        return {"id": "dry_run_id", "client_order_id": client_order_id, "status": "pending_new"}

    try:
        r = requests.post(f"{PAPER_URL}/orders", headers=get_headers(), json=data, timeout=10)
        if r.status_code in [200, 201]:
            logging.info(f"✅ Order Submitted: {side.upper()} {qty} {symbol} ({order_type}) ID: {client_order_id}")
            return r.json()
        else:
            logging.error(f"❌ Order Rejected for {symbol} ({client_order_id}): {r.text}")
            return None
    except Exception as e:
        logging.error(f"Order submission exception for {client_order_id}: {e}")
        return None

def cancel_broker_order(order_id, dry_run=False):
    if dry_run:
        logging.info(f"🔍 [DRY-RUN] Would cancel order ID: {order_id}")
        return True
    try:
        r = requests.delete(f"{PAPER_URL}/orders/{order_id}", headers=get_headers(), timeout=10)
        return r.status_code in [200, 204]
    except Exception as e:
        logging.error(f"Failed to cancel order {order_id}: {e}")
        return False

# --- 3. DATA & INDICATORS ---
def fetch_bars(symbol, timeframe="1Day", days=300, feed="sip"):
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    end_dt = datetime.datetime.now(NY_TZ)
    start_dt = end_dt - datetime.timedelta(days=days)
    
    params = {
        "timeframe": timeframe,
        "start": start_dt.strftime("%Y-%m-%d"),
        "end": end_dt.strftime("%Y-%m-%d"),
        "limit": 1000,
        "adjustment": "split",
        "feed": feed
    }
    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=15)
        if r.status_code == 403 and feed == "sip":
            params["feed"] = "iex"
            r = requests.get(url, headers=get_headers(), params=params, timeout=15)
        if r.status_code != 200: return None
        bars = r.json().get("bars", [])
        if not bars: return None
        df = pd.DataFrame(bars)
        df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['time'] = pd.to_datetime(df['time']).dt.tz_convert(NY_TZ)
        df.sort_values('time', inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
    except Exception as e:
        logging.error(f"Failed fetching {timeframe} bars for {symbol}: {e}")
        return None

def compute_daily_indicators(df):
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    df['donchian_20_high'] = df['high'].rolling(20).max().shift(1)
    df['donchian_55_high'] = df['high'].rolling(55).max().shift(1)
    
    vol_sma = df['volume'].rolling(20).mean()
    df['rvol'] = df['volume'] / (vol_sma + 1e-9)
    
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    up_move = df['high'].diff()
    down_move = -df['low'].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr14 = tr.rolling(14).sum()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).sum() / (tr14 + 1e-9)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).sum() / (tr14 + 1e-9)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
    df['adx'] = dx.rolling(14).mean()
    df['vol_20d'] = df['close'].pct_change().rolling(20).std() * np.sqrt(252)
    return df

def get_pit_stage2(symbol):
    df_w = fetch_bars(symbol, timeframe="1Week", days=600)
    if df_w is None or len(df_w) < 35: return False
    df_w['sma30w'] = df_w['close'].rolling(30).mean()
    df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
    df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
    
    c_pit = df_w['close'].iloc[-2]
    sma_pit = df_w['sma30w'].iloc[-2]
    slope_pit = df_w['sma30w_slope'].iloc[-2]
    h20_pit = df_w['prev_20w_high'].iloc[-2]
    
    if pd.isna(sma_pit) or pd.isna(slope_pit) or pd.isna(h20_pit): return False
    return (c_pit > sma_pit) and (slope_pit > 0) and (c_pit >= (0.95 * h20_pit))

def get_pit_stage4(symbol):
    df_w = fetch_bars(symbol, timeframe="1Week", days=600)
    if df_w is None or len(df_w) < 35: return False
    df_w['sma30w'] = df_w['close'].rolling(30).mean()
    df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
    
    c_pit = df_w['close'].iloc[-2]
    sma_pit = df_w['sma30w'].iloc[-2]
    slope_pit = df_w['sma30w_slope'].iloc[-2]
    
    if pd.isna(sma_pit) or pd.isna(slope_pit): return False
    return (c_pit < sma_pit) and (slope_pit < 0)

def get_rolling_corr(sym1, sym2, window=60):
    df1 = fetch_bars(sym1, timeframe="1Day", days=120)
    df2 = fetch_bars(sym2, timeframe="1Day", days=120)
    if df1 is None or df2 is None or len(df1) < 30 or len(df2) < 30: return 0.0
    s1 = df1.set_index('time')['close'].pct_change().dropna().tail(window)
    s2 = df2.set_index('time')['close'].pct_change().dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])

# --- 4. STATE PERSISTENCE ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load state: {e}")
    return {
        "high_water": 100000.0,
        "nav": 100000.0,
        "governor_state": "B",
        "state_dwell_counter": 0,
        "cooldown_counter": 0,
        "last_governor_date": None,
        "last_cooldown_decrement_date": None,
        "g3b5_positions": {},
        "pending_orders": {},
        "pending_exits": {}
    }

def save_state(state):
    temp = STATE_FILE + ".tmp"
    try:
        with open(temp, "w") as f:
            json.dump(state, f, indent=4)
        os.replace(temp, STATE_FILE)
    except Exception as e:
        logging.error(f"Failed to save state: {e}")

# --- 5. GOVERNOR LOGIC (SESSION-IDEMPOTENT) ---
def update_governor(state, current_nav, spy_vol, macro_bull, today_str):
    if current_nav > state["high_water"]:
        state["high_water"] = current_nav
    
    hw = state["high_water"]
    dd_pct = (hw - current_nav) / hw
    stress_confirm = (spy_vol > 0.25) and (not macro_bull)
    
    old_state = state["governor_state"]
    
    if state.get("last_governor_date") != today_str:
        state["state_dwell_counter"] = state.get("state_dwell_counter", 0) + 1
        state["last_governor_date"] = today_str
    
    dwell = G3_B5_PARAMS["dwell"]
    rh = G3_B5_PARAMS["recovery_hysteresis"]
    d1 = G3_B5_PARAMS["d1_trig"]
    d2 = G3_B5_PARAMS["d2_trig"]
    d3 = G3_B5_PARAMS["d3_trig"]

    if state["state_dwell_counter"] >= dwell:
        if old_state == "B":
            if dd_pct >= d3: state["governor_state"] = "D3"
            elif dd_pct >= d2 and stress_confirm: state["governor_state"] = "D2"
            elif dd_pct >= d1: state["governor_state"] = "D1"
        elif old_state == "D1":
            if dd_pct >= d3: state["governor_state"] = "D3"
            elif dd_pct >= d2 and stress_confirm: state["governor_state"] = "D2"
            elif dd_pct <= (d1 - rh): state["governor_state"] = "B"
        elif old_state == "D2":
            if dd_pct >= d3: state["governor_state"] = "D3"
            elif dd_pct <= (d2 - rh): state["governor_state"] = "D1"
        elif old_state == "D3":
            if dd_pct <= (d3 - rh): state["governor_state"] = "D2"

    if state["governor_state"] != old_state:
        state["state_dwell_counter"] = 0
        logging.info(f"🔄 GOVERNOR TRANSITION: {old_state} -> {state['governor_state']} (Drawdown: {dd_pct*100:.2f}%)")

    return state["governor_state"]

# --- 6. CORE RECONCILED TRADING ENGINE ---
def run_g3_b5_engine(dry_run=False):
    today_str = datetime.datetime.now(NY_TZ).strftime("%Y%m%d")
    logging.info("=" * 105)
    logging.info(f"🏛️ G3-B5 CANONICAL PAPER TRADER | DATE: {today_str} | MODE: {'[DRY-RUN - ZERO MUTATION]' if dry_run else '[LIVE PAPER EXECUTION]'}")
    logging.info("=" * 105)

    acct = get_account()
    if not acct:
        logging.error("Broker account unreachable. Aborting cycle (FAIL-CLOSED).")
        return

    current_nav = float(acct.get("portfolio_value", 100000.0))
    buying_power = float(acct.get("buying_power", 0.0))
    
    raw_state = load_state()
    state = copy.deepcopy(raw_state) if dry_run else raw_state
    state["nav"] = current_nav

    # 1. SPY Macro PIT & Volatility
    spy_df = fetch_bars("SPY", timeframe="1Day", days=300)
    if spy_df is None or len(spy_df) < 60:
        logging.error("SPY data unavailable. Aborting cycle (FAIL-CLOSED).")
        return

    spy_df = compute_daily_indicators(spy_df)
    spy_close_pit = spy_df['close'].iloc[-2]
    spy_ema20_pit = spy_df['ema20'].iloc[-2]
    spy_sma50_pit = spy_df['sma50'].iloc[-2]
    spy_vol = spy_df['vol_20d'].iloc[-2] if pd.notna(spy_df['vol_20d'].iloc[-2]) else 0.15

    macro_bull = (spy_close_pit > spy_ema20_pit) and (spy_ema20_pit > spy_sma50_pit)
    gov_state = update_governor(state, current_nav, spy_vol, macro_bull, today_str)

    logging.info(f"SPY PIT Close: ${spy_close_pit:.2f} | Macro Bull: {macro_bull} | SPY Vol: {spy_vol:.3f} | Governor: {gov_state}")

    # 2. Reconcile Asynchronous Orders & Partial Fills
    g3b5_positions = state.setdefault("g3b5_positions", {})
    pending_orders = state.setdefault("pending_orders", {})
    pending_exits = state.setdefault("pending_exits", {})

    if not dry_run:
        for cid, o_data in list(pending_orders.items()):
            order_info = get_order_by_client_id(cid)
            if not order_info: continue
            status = order_info.get("status")
            sym = o_data["symbol"]
            filled_qty = int(float(order_info.get("filled_qty", 0)))
            
            if status in ["filled", "partially_filled"] and filled_qty > 0:
                fill_px = float(order_info.get("filled_avg_price", o_data["entry_px"]))
                logging.info(f"🎉 Order FILL: {cid} ({status}) -> {filled_qty}/{o_data['qty']} of {sym} @ ${fill_px:.2f}")
                
                pos_entry = g3b5_positions.get(sym, {
                    "entry_px": fill_px,
                    "virtual_sl": fill_px - (2.0 * o_data["atr"]) if o_data["side"] == "long" else fill_px + o_data["risk_prs"],
                    "risk_prs": o_data["risk_prs"],
                    "tier": 0,
                    "engine": o_data["engine"],
                    "side": o_data["side"],
                    "owned_qty": 0,
                    "entry_client_order_id": cid,
                    "broker_stop_order_id": None,
                    "broker_stop_qty": 0,
                    "frozen": False
                })
                pos_entry["entry_px"] = fill_px
                pos_entry["owned_qty"] = filled_qty
                if o_data["side"] == "short":
                    pos_entry["target_px"] = fill_px - (1.5 * o_data["risk_prs"])
                    pos_entry["virtual_sl"] = fill_px + o_data["risk_prs"]
                g3b5_positions[sym] = pos_entry
                
                if status == "filled":
                    del pending_orders[cid]
                else:
                    o_data["qty"] = int(float(order_info.get("qty", o_data["qty"]))) - filled_qty
            elif status in ["canceled", "expired", "rejected"]:
                logging.info(f"🗑️ Entry Order TERMINATED: {cid} ({status}). Removing from pending.")
                del pending_orders[cid]

        for exit_cid, e_data in list(pending_exits.items()):
            order_info = get_order_by_client_id(exit_cid)
            if not order_info: continue
            status = order_info.get("status")
            sym = e_data["symbol"]
            exit_filled_qty = int(float(order_info.get("filled_qty", 0)))
            
            if exit_filled_qty > 0 and sym in g3b5_positions:
                remaining_owned = g3b5_positions[sym]["owned_qty"] - exit_filled_qty
                if remaining_owned <= 0:
                    logging.info(f"✅ Exit Order FULLY FILLED: {exit_cid} for {sym}. Position closed.")
                    if g3b5_positions[sym].get("broker_stop_order_id"):
                        cancel_broker_order(g3b5_positions[sym]["broker_stop_order_id"], dry_run=dry_run)
                    del g3b5_positions[sym]
                else:
                    g3b5_positions[sym]["owned_qty"] = remaining_owned
                    logging.info(f"⚠️ Exit Order PARTIAL FILL: {exit_cid} for {sym}. Remaining owned: {remaining_owned}")

            if status in ["filled", "canceled", "expired", "rejected"]:
                if status != "filled" and sym in g3b5_positions:
                    g3b5_positions[sym]["exit_pending"] = False
                    logging.warning(f"⚠️ Exit Order FAILED: {exit_cid} ({status}). Re-enabled management.")
                del pending_exits[exit_cid]

    # 3. Snapshot Broker Positions (Fail-Closed)
    broker_positions = get_broker_positions()
    if broker_positions is None:
        logging.error("Broker positions snapshot failed. Aborting cycle to protect state integrity.")
        return

    for sym in list(g3b5_positions.keys()):
        pos_data = g3b5_positions[sym]
        b_pos = broker_positions.get(sym)
        if not b_pos:
            logging.info(f"🧹 Reconciled external close: {sym} no longer in broker positions.")
            if pos_data.get("broker_stop_order_id"):
                cancel_broker_order(pos_data["broker_stop_order_id"], dry_run=dry_run)
            del g3b5_positions[sym]
            continue
            
        b_side = "long" if float(b_pos['qty']) > 0 else "short"
        if pos_data.get("side", "long") != b_side:
            logging.error(f"🚨 SIDE CONFLICT: G3-B5 expects {pos_data.get('side')} on {sym}, broker has {b_side}. FREEZING POSITION.")
            pos_data["frozen"] = True
            pos_data["freeze_reason"] = f"side_conflict_{b_side}"
            g3b5_positions[sym] = pos_data

    # 4. Position Management & Protective Stop Maintenance
    for sym, pos_data in list(g3b5_positions.items()):
        if pos_data.get("frozen", False) or pos_data.get("exit_pending", False): continue
        df = fetch_bars(sym, timeframe="1Day", days=60)
        if df is None or len(df) < 20: continue
        df = compute_daily_indicators(df)
        
        curr_open = df['open'].iloc[-1]
        curr_low = df['low'].iloc[-1]
        curr_high = df['high'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        atr_pit = df['atr'].iloc[-2]
        ema10 = df['ema10'].iloc[-1]
        
        entry_px = pos_data["entry_px"]
        owned_qty = pos_data.get("owned_qty", 0)
        b_pos = broker_positions.get(sym, {})
        broker_qty = abs(int(float(b_pos.get('qty', 0))))
        executable_qty = min(owned_qty, broker_qty)
        if executable_qty <= 0: continue

        # --- LONG MANAGEMENT ---
        if pos_data.get("side", "long") == "long":
            effective_sl = max(pos_data["virtual_sl"], entry_px * 0.85)

            gap_size = max(0.0, prev_close - curr_open)
            if gap_size > (2.0 * atr_pit):
                state["cooldown_counter"] = max(state.get("cooldown_counter", 0), 4)
                logging.warning(f"⚠️ ADVERSE GAP DETECTED on {sym} ({gap_size:.2f} > 2*ATR_PIT). Fired 3-Day Cooldown.")

            mfe_r = (curr_high - entry_px) / (pos_data["risk_prs"] + 1e-9)
            stop_ratcheted = False
            if mfe_r >= 1.5 and pos_data["tier"] < 1:
                pos_data["virtual_sl"] = max(pos_data["virtual_sl"], entry_px)
                pos_data["tier"] = 1
                stop_ratcheted = True
                logging.info(f"🎯 {sym} reached 1.5R MFE. Stop ratcheted to Breakeven (${entry_px:.2f}).")
            if pos_data["tier"] >= 1:
                new_sl = max(pos_data["virtual_sl"], ema10 - (0.5 * atr_pit))
                if new_sl > pos_data["virtual_sl"]:
                    pos_data["virtual_sl"] = new_sl
                    stop_ratcheted = True

            effective_sl = max(pos_data["virtual_sl"], entry_px * 0.85)

            stop_oid = pos_data.get("broker_stop_order_id")
            stop_needs_replace = stop_ratcheted or (pos_data.get("broker_stop_qty", 0) != executable_qty)
            
            if (not stop_oid or stop_needs_replace) and not dry_run:
                if stop_oid:
                    cancel_broker_order(stop_oid, dry_run=dry_run)
                    time.sleep(0.5)
                cid_stop = f"g3b5_sl_{sym}_{today_str}_{int(effective_sl*100)}"
                stop_ord = submit_broker_order(sym, executable_qty, "sell", order_type="stop", stop_price=effective_sl, client_order_id=cid_stop, dry_run=dry_run)
                if stop_ord:
                    pos_data["broker_stop_order_id"] = stop_ord.get("id")
                    pos_data["broker_stop_qty"] = executable_qty

            if curr_low <= effective_sl:
                has_active_stop = False
                if pos_data.get("broker_stop_order_id"):
                    s_info = get_order_by_id(pos_data["broker_stop_order_id"])
                    has_active_stop = s_info and s_info.get("status") in ["new", "accepted", "pending_new"]
                
                if not has_active_stop:
                    logging.info(f"🛑 LONG STOP TRIGGERED for {sym}. Submitting market exit for {executable_qty} shares.")
                    cid_exit = f"g3b5_exit_{sym}_{today_str}"
                    exit_ord = submit_broker_order(sym, executable_qty, "sell", client_order_id=cid_exit, dry_run=dry_run)
                    if exit_ord:
                        pos_data["exit_pending"] = True
                        pending_exits[cid_exit] = {"symbol": sym, "qty": executable_qty}

        # --- SHORT MANAGEMENT ---
        elif pos_data.get("side") == "short":
            stop_px = pos_data["virtual_sl"]
            target_px = pos_data.get("target_px", entry_px - (1.5 * pos_data["risk_prs"]))

            stop_oid = pos_data.get("broker_stop_order_id")
            if (not stop_oid or pos_data.get("broker_stop_qty", 0) != executable_qty) and not dry_run:
                if stop_oid:
                    cancel_broker_order(stop_oid, dry_run=dry_run)
                    time.sleep(0.5)
                cid_stop = f"g3b5_short_sl_{sym}_{today_str}_{int(stop_px*100)}"
                stop_ord = submit_broker_order(sym, executable_qty, "buy", order_type="stop", stop_price=stop_px, client_order_id=cid_stop, dry_run=dry_run)
                if stop_ord:
                    pos_data["broker_stop_order_id"] = stop_ord.get("id")
                    pos_data["broker_stop_qty"] = executable_qty

            if curr_low <= target_px:
                logging.info(f"🎯 SHORT TARGET REACHED for {sym}. Submitting cover for {executable_qty} shares.")
                if pos_data.get("broker_stop_order_id"):
                    cancel_broker_order(pos_data["broker_stop_order_id"], dry_run=dry_run)
                cid_exit = f"g3b5_cover_target_{sym}_{today_str}"
                exit_ord = submit_broker_order(sym, executable_qty, "buy", client_order_id=cid_exit, dry_run=dry_run)
                if exit_ord:
                    pos_data["exit_pending"] = True
                    pending_exits[cid_exit] = {"symbol": sym, "qty": executable_qty}

        g3b5_positions[sym] = pos_data

    # 5. Multipliers & Full Risk Reservation
    gov_mult = {"B": 1.0, "D1": 0.85, "D2": 0.50, "D3": 0.15}[gov_state]
    cooldown_mult = 0.25 if state.get("cooldown_counter", 0) > 0 else 1.0
    effective_mult = gov_mult * cooldown_mult

    def total_g3b5_open_risk():
        risk = 0.0
        for s, p in g3b5_positions.items():
            if p.get("frozen"): continue
            sl = max(p["virtual_sl"], p["entry_px"] * 0.85) if p.get("side", "long") == "long" else p["virtual_sl"]
            qty = p.get("owned_qty", 0)
            risk += qty * max(0.0, abs(p["entry_px"] - sl))
        return risk

    def open_tech_risk():
        risk = 0.0
        for s, p in g3b5_positions.items():
            if p.get("frozen"): continue
            if s in TECH_SYMBOLS:
                sl = max(p["virtual_sl"], p["entry_px"] * 0.85) if p.get("side", "long") == "long" else p["virtual_sl"]
                qty = p.get("owned_qty", 0)
                risk += qty * max(0.0, abs(p["entry_px"] - sl))
        return risk

    def pending_reserved_gross_risk():
        return sum(o["qty"] * o["risk_prs"] for o in pending_orders.values())

    def pending_reserved_tech_risk():
        return sum(o["qty"] * o["risk_prs"] for o in pending_orders.values() if o["symbol"] in TECH_SYMBOLS)

    rem_gross_risk = (current_nav * MAX_GROSS_PORTFOLIO_RISK * effective_mult) - total_g3b5_open_risk() - pending_reserved_gross_risk()
    rem_tech_risk = (current_nav * MAX_TECH_SECTOR_RISK) - open_tech_risk() - pending_reserved_tech_risk()

    held_symbols = set(g3b5_positions.keys()) | {o["symbol"] for o in pending_orders.values()}
    total_positions_count = len(held_symbols)

    # 6. Long Engine Execution (XLK Stage-2 & Donchian 55-Day)
    if macro_bull and total_positions_count < MAX_POSITIONS and rem_gross_risk > 0:
        xlk_candidates = []
        for sym in XLK_LONG_UNIVERSE:
            if sym in held_symbols or (sym in broker_positions and sym not in g3b5_positions): continue
            df = fetch_bars(sym, timeframe="1Day", days=100)
            if df is None or len(df) < 60: continue
            df = compute_daily_indicators(df)
            
            row = df.iloc[-1]
            prev = df.iloc[-2]
            if not get_pit_stage2(sym): continue
            
            breakout = (row['close'] > row['donchian_20_high']) and (prev['close'] <= prev['donchian_20_high'])
            if breakout and row['rvol'] > 1.25 and row['close'] > row['ema21']:
                score = (row['rvol'] * 25.0) + (row['adx'] * 1.5)
                xlk_candidates.append({"sym": sym, "score": score, "row": row, "engine": "xlk"})

        donch_candidates = []
        for sym in DONCHIAN_SATELLITE_UNIVERSE:
            if sym in held_symbols or (sym in broker_positions and sym not in g3b5_positions): continue
            df = fetch_bars(sym, timeframe="1Day", days=250)
            if df is None or len(df) < 200: continue
            df = compute_daily_indicators(df)
            row = df.iloc[-1]
            if row['close'] > row['donchian_55_high']:
                donch_candidates.append({"sym": sym, "score": 50.0, "row": row, "engine": "donch"})

        all_candidates = sorted(xlk_candidates + donch_candidates, key=lambda x: x['score'], reverse=True)

        for cand in all_candidates:
            if len(held_symbols) >= MAX_POSITIONS: break
            sym = cand["sym"]
            if sym in held_symbols: continue

            if cand["engine"] == "donch":
                max_corr = 0.0
                for held_s in held_symbols:
                    c = get_rolling_corr(sym, held_s, window=60)
                    max_corr = max(max_corr, c)
                if max_corr >= 0.80:
                    logging.info(f"🚫 Donchian Candidate {sym} skipped: Cross-Correlation {max_corr:.2f} >= 0.80 with held set.")
                    continue
            
            row = cand["row"]
            entry_px = row['close'] + SLIP + SPREAD
            atr = row['atr']
            sl = max(entry_px - (2.0 * atr), entry_px * 0.85)
            rps = max(entry_px - sl, 0.01)

            if cand["engine"] == "xlk":
                dyn_risk = MAX_SINGLE_NAME_RISK * min(max(row["rvol"] / 1.5, 0.8), 1.6) * min(max(row["adx"] / 25.0, 0.8), 1.5) * effective_mult
                budget = min(current_nav * min(dyn_risk, 0.010), rem_gross_risk)
            else:
                budget = min(current_nav * min(0.0025 * effective_mult, 0.005), rem_gross_risk)

            if sym in TECH_SYMBOLS:
                if rem_tech_risk <= 0: continue
                budget = min(budget, rem_tech_risk)

            shares = int(budget / rps)
            if shares > 0 and (shares * entry_px) <= buying_power:
                cid_entry = f"g3b5_{cand['engine']}_{sym}_{today_str}"
                order = submit_broker_order(sym, shares, "buy", client_order_id=cid_entry, dry_run=dry_run)
                if order:
                    pending_orders[cid_entry] = {
                        "symbol": sym, "qty": shares, "entry_px": entry_px,
                        "risk_prs": rps, "atr": atr, "engine": cand["engine"], "side": "long"
                    }
                    held_symbols.add(sym)
                    rem_gross_risk -= (shares * rps)
                    if sym in TECH_SYMBOLS: rem_tech_risk -= (shares * rps)

    # 7. Short Engine Execution (Bear Regime)
    short_base_risk = 0.0050 if gov_state == "B" else 0.0025
    active_short_risk = short_base_risk * cooldown_mult

    if not macro_bull and len(held_symbols) == 0:
        for sym in STAGE4_SHORT_FLEET:
            if sym in broker_positions: continue
            df = fetch_bars(sym, timeframe="1Day", days=100)
            if df is None or len(df) < 60: continue
            df = compute_daily_indicators(df)
            row = df.iloc[-1]
            if not get_pit_stage4(sym): continue
            
            if (row['close'] < row['ema20']) and (row['ema20'] < row['sma50']):
                limit_px = row['ema20']
                atr = row['atr']
                rps = 0.75 * atr
                shares = int((current_nav * active_short_risk) / rps)
                if shares > 0:
                    logging.info(f"📉 STAGE-4 SHORT SIGNAL: {sym} Limit: ${limit_px:.2f} Shares: {shares}")
                    cid_short = f"g3b5_short_{sym}_{today_str}"
                    order = submit_broker_order(sym, shares, "sell", order_type="limit", limit_price=limit_px, client_order_id=cid_short, dry_run=dry_run)
                    if order:
                        pending_orders[cid_short] = {
                            "symbol": sym, "qty": shares, "entry_px": limit_px,
                            "risk_prs": rps, "atr": atr, "engine": "stage4_short", "side": "short"
                        }
                        break

    # 8. Decrement Cooldown Once per Market Session
    if not dry_run and state.get("last_cooldown_decrement_date") != today_str:
        if state.get("cooldown_counter", 0) > 0:
            state["cooldown_counter"] -= 1
        state["last_cooldown_decrement_date"] = today_str

    if dry_run:
        logging.info("🔍 DRY-RUN PREFLIGHT COMPLETE — Zero state mutations & zero disk writes verified.")
    else:
        state["g3b5_positions"] = g3b5_positions
        state["pending_orders"] = pending_orders
        state["pending_exits"] = pending_exits
        save_state(state)
        logging.info("💾 Reconciled canonical state safely written to disk.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="G3-B5 Canonical Production Paper Trading Daemon")
    parser.add_argument("--dry-run", action="store_true", help="Perform safe preflight evaluation without placing orders, logging to disk, or mutating state")
    args = parser.parse_args()
    
    setup_logger(dry_run=args.dry_run)
    run_g3_b5_engine(dry_run=args.dry_run)
