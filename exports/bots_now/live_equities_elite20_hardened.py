#!/usr/bin/env python3
"""
Elite 20 Equities: Hardened Production Execution Daemon
Architecture:
- Scan Timing: Completed D1 Bar Post-Close (16:15 ET) -> Execution at Market Open (09:30:15 ET)
- Lookback: 220-day SIP batch fetch with explicit start date
- Timezone: America/New_York via ZoneInfo
- Exchange Calendar: Alpaca /v2/clock & /v2/calendar API validation
- State & Idempotency: Persistent JSON ledger (/home/moonrider1968/bots/elite20_state.json)
- Sizing: Dynamic 0.50% NAV risk per trade | Gross 1.50% NAV open risk cap (Max 3 positions, Max 1/sector)
"""

import os
import sys
import time
import json
import math
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------
LOG_DIR = Path("/home/moonrider1968/bots/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "live_equities_elite20.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Elite20_Daemon")

# ---------------------------------------------------------
# Environment & Configuration
# ---------------------------------------------------------
ENV_FILE = Path("/home/moonrider1968/bots/.env")
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_ENVIRONMENT = os.getenv("ALPACA_ENVIRONMENT", "paper").lower()

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    logger.critical("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in environment.")
    sys.exit(1)

BASE_URL = "https://paper-api.alpaca.markets/v2" if ALPACA_ENVIRONMENT == "paper" else "https://api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"

SESSION = requests.Session()
SESSION.headers.update({
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
})

# OWNERSHIP_V1 (2026-10-01): shared paper account. Orders tagged "e20-"; capacity / sector /
# risk accounting uses ONLY this bot's own positions (~/bots/ownership/elite20_ledger.json);
# entries skipped if anyone already holds or has an open order on the symbol.
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership
OWN = Ownership("elite20", "e20-", key=ALPACA_API_KEY, secret=ALPACA_SECRET_KEY,
                base_url=BASE_URL, logger=logger)

NY_TZ = ZoneInfo("America/New_York")
STATE_FILE = Path("/home/moonrider1968/bots/elite20_state.json")

SYMBOL_TO_SECTOR = {
    "SPY": "INDEX", "QQQ": "INDEX",
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH",
    "GOOGL": "COMM", "META": "COMM", "NFLX": "COMM",
    "WMT": "STAPLES", "COST": "STAPLES", "PG": "STAPLES",
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY",
    "AMZN": "DISC",
    "CAT": "IND", "GE": "IND",
    "JPM": "FIN", "GS": "FIN"
}

ELITE_20_SYMBOLS = sorted(list(SYMBOL_TO_SECTOR.keys()))
BASE_RISK_PCT = 0.0050      # 0.50% NAV risk per trade
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Open Portfolio Risk
MAX_CONCURRENT_POSITIONS = 3
MAX_NOTIONAL_PCT = 0.20      # FIX_2026-10-01: max 20% NAV notional per position
MAX_PER_SECTOR = 1

# ---------------------------------------------------------
# State Management
# ---------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            logger.error(f"Error reading state file: {e}")
    return {"processed_signals": {}, "active_trade_brackets": {}, "pending_open_orders": []}

def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.error(f"Error saving state file: {e}")

# ---------------------------------------------------------
# Alpaca API & Data Ingestion
# ---------------------------------------------------------
def alpaca_request(method: str, endpoint: str, is_data: bool = False, params: Optional[dict] = None, json_data: Optional[dict] = None) -> Optional[dict]:
    url = f"{DATA_URL if is_data else BASE_URL}{endpoint}"
    for attempt in range(1, 4):
        try:
            resp = SESSION.request(method, url, params=params, json=json_data, timeout=15)
            if resp.status_code in [200, 201]:
                return resp.json()
            elif resp.status_code in [400, 401, 404, 422]:
                logger.error(f"Alpaca API {resp.status_code} on {endpoint}: {resp.text}")
                return None
            else:
                logger.warning(f"Alpaca API {resp.status_code} [{attempt}/3]: {resp.text}")
                time.sleep(2 * attempt)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error on {endpoint} [{attempt}/3]: {e}")
            time.sleep(2 * attempt)
    return None

def fetch_batch_daily_bars(symbols: List[str], lookback_days: int = 220) -> Dict[str, pd.DataFrame]:
    symbols_str = ",".join(symbols)
    endpoint = "/stocks/bars"
    start_date = (datetime.now(NY_TZ) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    for feed in ["sip", "iex"]:
        params = {
            "symbols": symbols_str,
            "timeframe": "1Day",
            "start": start_date,
            "limit": 10000,
            "adjustment": "all",
            "feed": feed
        }
        data = alpaca_request("GET", endpoint, is_data=True, params=params)
        if data and "bars" in data and data["bars"]:
            result = {}
            for sym, bars in data["bars"].items():
                records = []
                for b in bars:
                    records.append({
                        "timestamp": pd.to_datetime(b["t"]).astimezone(NY_TZ),
                        "open": float(b["o"]),
                        "high": float(b["h"]),
                        "low": float(b["l"]),
                        "close": float(b["c"]),
                        "volume": int(b["v"])
                    })
                result[sym] = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
            return result
    return {}

def fetch_account_and_positions() -> Tuple[float, float, List[dict], List[dict]]:
    account = alpaca_request("GET", "/account")
    positions = alpaca_request("GET", "/positions")
    orders = alpaca_request("GET", "/orders?status=open")
    if account and "equity" in account:
        nav = float(account["equity"])
        cash = float(account["cash"])
        pos_list = positions if isinstance(positions, list) else []
        # OWNERSHIP_V1: only count positions this bot opened
        owned = OWN.reconcile()
        pos_list = [p for p in pos_list if str(p.get("symbol", "")).replace("/", "").upper() in owned]
        ord_list = orders if isinstance(orders, list) else []
        return nav, cash, pos_list, ord_list
    raise RuntimeError("Failed to retrieve Alpaca account metrics.")

# ---------------------------------------------------------
# Dynamic Risk Accounting & Execution
# ---------------------------------------------------------
def calculate_current_gross_risk(positions: List[dict], state: dict) -> Tuple[float, Set[str]]:
    total_open_dollar_risk = 0.0
    active_sectors = set()
    active_brackets = state.get("active_trade_brackets", {})

    for p in positions:
        sym = p["symbol"]
        qty = abs(float(p["qty"]))
        entry_px = float(p["avg_entry_price"])
        sec = SYMBOL_TO_SECTOR.get(sym, "UNKNOWN")
        active_sectors.add(sec)

        bracket = active_brackets.get(sym)
        if bracket:
            stop_px = bracket["stop_px"]
            dollar_risk = max(0.0, (entry_px - stop_px) * qty)
        else:
            dollar_risk = entry_px * 0.03 * qty

        total_open_dollar_risk += dollar_risk

    return total_open_dollar_risk, active_sectors

def execute_queued_open_setups():
    logger.info("=" * 80)
    logger.info("⚡ Market Open Cycle (09:30 ET): Processing queued setups...")
    state = load_state()
    pending_signals = state.get("pending_open_orders", [])
    
    if not pending_signals:
        logger.info("No queued setups to execute.")
        return

    try:
        nav, cash, positions, _ = fetch_account_and_positions()
    except Exception as e:
        logger.error(f"Failed to retrieve account metrics at open: {e}")
        return

    total_open_risk, active_sectors = calculate_current_gross_risk(positions, state)
    active_symbols = set(p["symbol"] for p in positions)
    num_positions = len(positions)

    for sig in pending_signals:
        sym = sig["symbol"]
        sec = sig["sector"]

        if sym in active_symbols or sec in active_sectors or num_positions >= MAX_CONCURRENT_POSITIONS:
            logger.info(f"Setup {sym} bypassed: Portfolio or sector capacity reached.")
            continue

        target_risk_usd = nav * BASE_RISK_PCT
        if (total_open_risk + target_risk_usd) > (nav * GROSS_RISK_CAP_PCT + 1e-4):
            logger.info(f"Gross Risk Cap exceeded for {sym}. Bypassed.")
            continue

        quote = alpaca_request("GET", f"/stocks/{sym}/quotes/latest", is_data=True)
        if not quote or "quote" not in quote:
            logger.error(f"Failed to fetch live opening quote for {sym}. Skipped.")
            continue

        current_ask = float(quote["quote"]["ap"])
        
        # Sanity Checks against gap openings
        if current_ask <= sig["stop_px"]:
            logger.warning(f"🚫 ABORT {sym}: Open price (${current_ask:.2f}) gapped below stop (${sig['stop_px']:.2f}).")
            continue
        if current_ask >= sig["target_px"]:
            logger.warning(f"🚫 ABORT {sym}: Open price (${current_ask:.2f}) gapped above target (${sig['target_px']:.2f}).")
            continue

        actual_stop_dist = current_ask - sig["stop_px"]
        if actual_stop_dist <= 0.05:
            continue

        qty = int(math.floor(target_risk_usd / actual_stop_dist))
        # FIX_2026-10-01: hard notional cap (20% of NAV per position) on top of the 0.5% risk sizing
        qty = min(qty, int(math.floor(nav * MAX_NOTIONAL_PCT / current_ask)))
        if qty < 1:
            logger.warning(f"Calculated shares < 1 for {sym}. Skipped.")
            continue

        order_payload = {
            "symbol": sym,
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{sig['target_px']:.2f}"},
            "stop_loss": {"stop_price": f"{sig['stop_px']:.2f}"}
        }

        logger.info(f"🚀 Submitting Bracket Order: {sym} | Qty: {qty} | Ask: ${current_ask:.2f} | Stop: ${sig['stop_px']:.2f} | Target: ${sig['target_px']:.2f}")
        resp = OWN.entry(order_payload, tag="en")  # OWNERSHIP_V1: tagged, busy-checked

        if resp and "id" in resp:
            logger.info(f"✅ Order Accepted: ID {resp['id']}")
            num_positions += 1
            active_symbols.add(sym)
            active_sectors.add(sec)
            total_open_risk += target_risk_usd
            state["active_trade_brackets"][sym] = {
                "stop_px": sig["stop_px"],
                "target_px": sig["target_px"],
                "intended_qty": qty,
                "order_id": resp["id"],
                "entry_time": datetime.now(NY_TZ).isoformat()
            }

    state["pending_open_orders"] = []
    save_state(state)

def run_post_close_scan():
    logger.info("=" * 80)
    logger.info("📊 Running Post-Close D1 Completed Bar Scan (16:15 ET)...")

    today_ny = datetime.now(NY_TZ).date().strftime("%Y-%m-%d")
    state = load_state()
    
    try:
        nav, cash, positions, _ = fetch_account_and_positions()
    except Exception as e:
        logger.error(f"Failed to fetch account metrics: {e}")
        return

    total_open_risk, active_sectors = calculate_current_gross_risk(positions, state)
    active_symbols = set(p["symbol"] for p in positions)
    state["active_trade_brackets"] = {k: v for k, v in state.get("active_trade_brackets", {}).items() if k in active_symbols}

    logger.info(f"NAV: ${nav:,.2f} | Open Positions: {len(positions)}/{MAX_CONCURRENT_POSITIONS} | Gross Open Risk: ${(total_open_risk):,.2f} ({(total_open_risk/nav)*100:.2f}%)")

    if len(positions) >= MAX_CONCURRENT_POSITIONS:
        logger.info("Portfolio at full capacity. Skipping scan.")
        save_state(state)
        return

    bars_by_sym = fetch_batch_daily_bars(ELITE_20_SYMBOLS, lookback_days=220)
    new_setups = []

    for sym in ELITE_20_SYMBOLS:
        if sym in active_symbols:
            continue
        sec = SYMBOL_TO_SECTOR[sym]
        if sec in active_sectors:
            continue

        signal_key = f"{sym}_{today_ny}"
        if signal_key in state.get("processed_signals", {}):
            continue

        df = bars_by_sym.get(sym)
        if df is None or len(df) < 55:
            continue

        df["ma50"] = df["close"].rolling(50).mean()
        df["std50"] = df["close"].rolling(50).std()
        df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
        df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        if (prev["close"] < prev["lower_25s"]) and (cur["close"] > cur["lower_25s"]) and (cur["close"] < cur["ma50"]):
            stop_dist = float(cur["close"] - cur["lower_35s"])
            if stop_dist > 0.10:
                sig = {
                    "symbol": sym,
                    "signal_date": today_ny,
                    "sector": sec,
                    "side": "buy",
                    "signal_close": round(float(cur["close"]), 2),
                    "stop_px": round(float(cur["lower_35s"]), 2),
                    "target_px": round(float(cur["ma50"]), 2),
                    "stop_dist": round(stop_dist, 2)
                }
                logger.info(f"🎯 Reclaim Setup Identified: {sym} ({sec}) | Close: ${sig['signal_close']:.2f} | Stop: ${sig['stop_px']:.2f} | Target: ${sig['target_px']:.2f}")
                new_setups.append(sig)
                state.setdefault("processed_signals", {})[signal_key] = datetime.now(NY_TZ).isoformat()

    state["pending_open_orders"] = new_setups
    save_state(state)
    logger.info(f"Post-close scan complete. {len(new_setups)} setup(s) queued for next market open.")

# ---------------------------------------------------------
# Scheduling Loop
# ---------------------------------------------------------
def sleep_until_next_event():
    now = datetime.now(NY_TZ)
    today_str = now.strftime("%Y-%m-%d")
    
    clock = alpaca_request("GET", "/clock")
    if not clock:
        time.sleep(60)
        return

    t_open = now.replace(hour=9, minute=30, second=15, microsecond=0)
    t_post_close = now.replace(hour=16, minute=15, second=0, microsecond=0)

    cal = alpaca_request("GET", f"/calendar?start={today_str}&end={(now + timedelta(days=7)).strftime('%Y-%m-%d')}")
    if not cal:
        time.sleep(300)
        return

    today_session = next((c for c in cal if c["date"] == today_str), None)
    
    if not today_session or now >= t_post_close:
        next_session = next((c for c in cal if c["date"] > today_str), None)
        if not next_session:
            time.sleep(3600)
            return
        next_open_dt = datetime.strptime(f"{next_session['date']} 09:30:15", "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY_TZ)
        sleep_sec = max(10, (next_open_dt - now).total_seconds())
        logger.info(f"Market closed. Sleeping until next Open: {next_open_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} ({sleep_sec/3600:.2f} hrs).")
        time.sleep(sleep_sec)
        execute_queued_open_setups()
        return

    if now < t_open:
        sleep_sec = max(10, (t_open - now).total_seconds())
        logger.info(f"Sleeping until today's Market Open: {t_open.strftime('%H:%M:%S %Z')} ({sleep_sec/60:.1f} mins).")
        time.sleep(sleep_sec)
        execute_queued_open_setups()
        return

    if t_open <= now < t_post_close:
        sleep_sec = max(10, (t_post_close - now).total_seconds())
        logger.info(f"Session active. Sleeping until Post-Close Scan: {t_post_close.strftime('%H:%M:%S %Z')} ({sleep_sec/3600:.2f} hrs).")
        time.sleep(sleep_sec)
        run_post_close_scan()
        return

def main():
    logger.info("=" * 80)
    logger.info("Starting Hardened Elite 20 Equities Mean Reversion Daemon...")
    logger.info(f"Environment: {ALPACA_ENVIRONMENT.upper()} | Account URL: {BASE_URL}")
    logger.info(f"Universe: {len(ELITE_20_SYMBOLS)} Tickers | Firewall: Max {MAX_PER_SECTOR}/Sector | Gross Cap: {GROSS_RISK_CAP_PCT*100:.1f}% NAV")
    logger.info("=" * 80)

    try:
        nav, cash, positions, _ = fetch_account_and_positions()
        logger.info(f"Startup Connection OK: NAV=${nav:,.2f} | Active Positions: {len(positions)}")
    except Exception as e:
        logger.critical(f"Failed to connect to Alpaca API at startup: {e}")
        sys.exit(1)

    while True:
        try:
            sleep_until_next_event()
        except Exception as e:
            logger.error(f"Execution loop exception: {e}", exc_info=True)
            time.sleep(60)

if __name__ == "__main__":
    main()
