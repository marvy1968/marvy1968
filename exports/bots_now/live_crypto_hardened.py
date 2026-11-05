#!/usr/bin/env python3
"""
Hardened Crypto Mean-Reversion Execution Daemon
Strategy: 20 SMA Centerline | -3.2 Sigma Reclaim Entry | -4.2 Sigma Hard Stop
Universe: Alpaca US Supported Pairs | Max 1 per Narrative Cluster | Max 3 Total (1.50% Gross Cap)
Execution Schedule: Daily Scan & Order Routing at 00:00:30 UTC
Continuous Defense: Stop Loss / Target Take-Profit Monitoring Every 30 Seconds
"""

import os
import sys
import time
import json
import math
import logging
from datetime import datetime, timezone, timedelta
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
LOG_FILE = LOG_DIR / "live_crypto.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Crypto_Daemon")

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
DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us"

SESSION = requests.Session()
SESSION.headers.update({
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
})

STATE_FILE = Path("/home/moonrider1968/bots/crypto_state.json")

# Verified supported Alpaca crypto pairs grouped by narrative cluster
CRYPTO_CLUSTERS = {
    "BTC/USD": "CORE_L1",
    "ETH/USD": "CORE_L1",
    "SOL/USD": "ALT_L1",
    "AVAX/USD": "ALT_L1",
    "DOT/USD": "ALT_L1",
    "UNI/USD": "DEFI",
    "AAVE/USD": "DEFI",
    "LINK/USD": "INFRA",
    "LTC/USD": "LEGACY",
    "BCH/USD": "LEGACY"
}

CRYPTO_SYMBOLS = sorted(list(CRYPTO_CLUSTERS.keys()))
BASE_RISK_PCT = 0.0050      # 0.50% NAV risk per trade
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Open Portfolio Risk
MAX_CONCURRENT_POSITIONS = 3
MAX_PER_CLUSTER = 1

# Sizing decimals per asset class
SYMBOL_PRECISION = {
    "BTC/USD": 4,
    "ETH/USD": 4,
    "SOL/USD": 2,
    "AVAX/USD": 2,
    "DOT/USD": 2,
    "UNI/USD": 2,
    "AAVE/USD": 2,
    "LINK/USD": 2,
    "LTC/USD": 3,
    "BCH/USD": 3
}

# ---------------------------------------------------------
# State Management
# ---------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            logger.error(f"Error reading crypto state file: {e}")
    return {"processed_signals": {}, "active_trade_brackets": {}}

def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.error(f"Error saving crypto state file: {e}")

# ---------------------------------------------------------
# Alpaca API & Data Ingestion
# ---------------------------------------------------------
def alpaca_request(method: str, url: str, params: Optional[dict] = None, json_data: Optional[dict] = None) -> Optional[dict]:
    for attempt in range(1, 4):
        try:
            resp = SESSION.request(method, url, params=params, json=json_data, timeout=15)
            if resp.status_code in [200, 201]:
                return resp.json()
            elif resp.status_code in [400, 401, 404, 422]:
                logger.error(f"Alpaca API {resp.status_code} on {url}: {resp.text}")
                return None
            else:
                logger.warning(f"Alpaca API {resp.status_code} [{attempt}/3]: {resp.text}")
                time.sleep(2 * attempt)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error on {url} [{attempt}/3]: {e}")
            time.sleep(2 * attempt)
    return None

def fetch_batch_crypto_bars(symbols: List[str], lookback_days: int = 60) -> Dict[str, pd.DataFrame]:
    start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start_date,
        "limit": 10000
    }
    data = alpaca_request("GET", f"{DATA_URL}/bars", params=params)
    if not data or "bars" not in data:
        return {}

    result = {}
    for sym, bars in data["bars"].items():
        records = []
        for b in bars:
            records.append({
                "timestamp": pd.to_datetime(b["t"]).astimezone(timezone.utc),
                "open": float(b["o"]),
                "high": float(b["h"]),
                "low": float(b["l"]),
                "close": float(b["c"]),
                "volume": float(b["v"])
            })
        result[sym] = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
    return result

def fetch_account_and_positions() -> Tuple[float, float, List[dict]]:
    account = alpaca_request("GET", f"{BASE_URL}/account")
    positions = alpaca_request("GET", f"{BASE_URL}/positions")
    if account and "equity" in account:
        nav = float(account["equity"])
        cash = float(account["cash"])
        pos_list = [p for p in positions if "/" in p.get("symbol", "") or "USD" in p.get("symbol", "")] if isinstance(positions, list) else []
        return nav, cash, pos_list
    raise RuntimeError("Failed to retrieve Alpaca account metrics.")

def calculate_current_gross_risk(positions: List[dict], state: dict) -> Tuple[float, Set[str]]:
    total_open_dollar_risk = 0.0
    active_clusters = set()
    active_brackets = state.get("active_trade_brackets", {})

    for p in positions:
        sym = p["symbol"]
        qty = abs(float(p["qty"]))
        entry_px = float(p["avg_entry_price"])
        cluster = CRYPTO_CLUSTERS.get(sym, "UNKNOWN")
        active_clusters.add(cluster)

        bracket = active_brackets.get(sym)
        if bracket:
            stop_px = bracket["stop_px"]
            dollar_risk = max(0.0, (entry_px - stop_px) * qty)
        else:
            dollar_risk = entry_px * 0.05 * qty

        total_open_dollar_risk += dollar_risk

    return total_open_dollar_risk, active_clusters

# ---------------------------------------------------------
# Dynamic Risk & Execution
# ---------------------------------------------------------
def manage_active_exits():
    state = load_state()
    active_brackets = state.get("active_trade_brackets", {})
    if not active_brackets:
        return

    try:
        _, _, positions = fetch_account_and_positions()
    except Exception as e:
        logger.error(f"Error fetching account during exit monitoring: {e}")
        return

    pos_by_sym = {p["symbol"]: p for p in positions}

    for sym, bracket in list(active_brackets.items()):
        if sym not in pos_by_sym:
            logger.info(f"Position in {sym} no longer active on broker. Clearing from active brackets.")
            del active_brackets[sym]
            continue

        quote_resp = alpaca_request("GET", f"{DATA_URL}/quotes/latest", params={"symbols": sym})
        if not quote_resp or "quotes" not in quote_resp or sym not in quote_resp["quotes"]:
            continue

        bid = float(quote_resp["quotes"][sym]["bp"])
        ask = float(quote_resp["quotes"][sym]["ap"])
        mid = (bid + ask) / 2.0

        pos_qty = float(pos_by_sym[sym]["qty"])
        hit_stop = mid <= bracket["stop_px"]
        hit_target = mid >= bracket["target_px"]

        if hit_stop or hit_target:
            reason = "STOP_LOSS" if hit_stop else "TAKE_PROFIT"
            logger.info(f"🎯 Crypto Exit Triggered: {sym} ({reason}) | Mid=${mid:.4f} | Target=${bracket['target_px']:.4f} | Stop=${bracket['stop_px']:.4f}")
            order_payload = {
                "symbol": sym,
                "qty": str(pos_qty),
                "side": "sell",
                "type": "market",
                "time_in_force": "gtc"
            }
            resp = alpaca_request("POST", f"{BASE_URL}/orders", json_data=order_payload)
            if resp and "id" in resp:
                logger.info(f"✅ Exit order submitted: ID {resp['id']}")
                del active_brackets[sym]

    state["active_trade_brackets"] = active_brackets
    save_state(state)

def run_daily_scan_and_execution():
    logger.info("=" * 80)
    logger.info("🌌 Running 00:00 UTC Completed Daily Bar Scan & Order Execution...")
    state = load_state()
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        nav, cash, positions = fetch_account_and_positions()
    except Exception as e:
        logger.error(f"Failed to fetch account metrics: {e}")
        return

    total_open_risk, active_clusters = calculate_current_gross_risk(positions, state)
    active_symbols = set(p["symbol"] for p in positions)
    logger.info(f"NAV: ${nav:,.2f} | Open Crypto Positions: {len(positions)}/{MAX_CONCURRENT_POSITIONS} | Gross Open Risk: ${total_open_risk:,.2f} ({(total_open_risk/nav)*100:.2f}%)")

    if len(positions) >= MAX_CONCURRENT_POSITIONS:
        logger.info("Crypto portfolio capacity reached. Skipping entry scan.")
        return

    bars_by_sym = fetch_batch_crypto_bars(CRYPTO_SYMBOLS, lookback_days=60)

    for sym in CRYPTO_SYMBOLS:
        if sym in active_symbols or len(positions) >= MAX_CONCURRENT_POSITIONS:
            continue
        cluster = CRYPTO_CLUSTERS[sym]
        if cluster in active_clusters:
            continue

        signal_key = f"{sym}_{today_utc}"
        if signal_key in state.get("processed_signals", {}):
            continue

        df = bars_by_sym.get(sym)
        if df is None or len(df) < 22:
            continue

        df["ma20"] = df["close"].rolling(20).mean()
        df["std20"] = df["close"].rolling(20).std()
        df["lower_32s"] = df["ma20"] - (3.2 * df["std20"])
        df["lower_42s"] = df["ma20"] - (4.2 * df["std20"])

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(cur["lower_32s"]):
            continue

        # Reclaim condition: Prior close < -3.2s, Current close > -3.2s and < 20 SMA
        if (prev["close"] < prev["lower_32s"]) and (cur["close"] > cur["lower_32s"]) and (cur["close"] < cur["ma20"]):
            c_px = float(cur["close"])
            stop_px = float(cur["lower_42s"])
            target_px = float(cur["ma20"])
            stop_dist = c_px - stop_px

            if stop_dist <= (c_px * 0.005):
                continue

            target_risk_usd = nav * BASE_RISK_PCT
            if (total_open_risk + target_risk_usd) > (nav * GROSS_RISK_CAP_PCT + 1e-4):
                logger.info(f"Gross Risk Cap exceeded for {sym}. Bypassed.")
                continue

            prec = SYMBOL_PRECISION.get(sym, 2)
            raw_units = target_risk_usd / stop_dist
            units = math.floor(raw_units * (10 ** prec)) / (10 ** prec)

            if units <= 0:
                continue

            order_payload = {
                "symbol": sym,
                "qty": str(units),
                "side": "buy",
                "type": "market",
                "time_in_force": "gtc"
            }

            logger.info(f"🚀 Submitting Crypto Order: {sym} ({cluster}) | Units: {units} | Close: ${c_px:.4f} | Target: ${target_px:.4f} | Stop: ${stop_px:.4f}")
            resp = alpaca_request("POST", f"{BASE_URL}/orders", json_data=order_payload)
            if resp and "id" in resp:
                logger.info(f"✅ Crypto Order Accepted: ID {resp['id']}")
                active_symbols.add(sym)
                active_clusters.add(cluster)
                positions.append({"symbol": sym})
                total_open_risk += target_risk_usd
                state.setdefault("active_trade_brackets", {})[sym] = {
                    "stop_px": round(stop_px, 4),
                    "target_px": round(target_px, 4),
                    "units": units,
                    "order_id": resp["id"],
                    "entry_time": datetime.now(timezone.utc).isoformat()
                }
                state.setdefault("processed_signals", {})[signal_key] = datetime.now(timezone.utc).isoformat()

    save_state(state)

# ---------------------------------------------------------
# Scheduling Loop
# ---------------------------------------------------------
def main():
    logger.info("=" * 80)
    logger.info("Starting Hardened Crypto Mean-Reversion Daemon (20 SMA / -3.2 Sigma)...")
    logger.info(f"Environment: {ALPACA_ENVIRONMENT.upper()} | Account URL: {BASE_URL}")
    logger.info(f"Universe: {len(CRYPTO_SYMBOLS)} Pairs | Firewall: Max {MAX_PER_CLUSTER}/Cluster | Cap: {GROSS_RISK_CAP_PCT*100:.1f}% NAV")
    logger.info("=" * 80)

    try:
        nav, cash, positions = fetch_account_and_positions()
        logger.info(f"Startup OK: NAV=${nav:,.2f} | Open Crypto Positions: {len(positions)}")
    except Exception as e:
        logger.critical(f"Failed to connect to Alpaca API at startup: {e}")
        sys.exit(1)

    scan_done_for_day = ""

    while True:
        try:
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")

            # Manage trailing exits continuously every 30 seconds
            manage_active_exits()

            # Execute daily completed bar scan between 00:00:30 and 00:02:00 UTC
            if now.hour == 0 and now.minute in [0, 1] and scan_done_for_day != today_str:
                time.sleep(30) # Allow Alpaca's daily bar to finalize
                run_daily_scan_and_execution()
                scan_done_for_day = today_str

            time.sleep(30)
        except Exception as e:
            logger.error(f"Execution loop error: {e}", exc_info=True)
            time.sleep(30)

if __name__ == "__main__":
    main()
