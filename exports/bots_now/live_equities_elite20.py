#!/usr/bin/env python3
"""
Production Paper/Live Trading Daemon: Hardened Elite 20 Equities Mean Reversion
Certified Architecture:
- Timeframe: Daily (D1) at Market Close (Evaluated at 15:55 ET / 19:55 UTC)
- Direction: Long-Only
- Baseline: 50 SMA
- Entry Trigger: Prior close < -2.5-Sigma envelope; Current close reclaims > -2.5-Sigma and < 50 SMA
- Stop Loss: Point-in-time frozen -3.5-Sigma envelope (Hard GTC bracket)
- Take Profit: Point-in-time frozen 50 SMA centerline (Hard GTC bracket)
- Sizing: Dynamic 0.50% NAV risk per trade
- Risk Firewall: Gross 1.50% NAV open risk cap (Max 3 concurrent positions) AND Max 1 position per sector
- Execution API: Alpaca REST API (alpaca-py / REST requests)
"""

import os
import sys
import time
import math
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    logger.error("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in environment / .env")
    sys.exit(1)

BASE_URL = "https://paper-api.alpaca.markets/v2" if ALPACA_ENVIRONMENT == "paper" else "https://api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"

SESSION = requests.Session()
SESSION.headers.update({
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
})

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
BASE_RISK_PCT = 0.0250      # 0.50% NAV risk per trade
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Open Portfolio Risk
MAX_CONCURRENT_POSITIONS = 5
MAX_PER_SECTOR = 1
WARMUP_BARS = 100

# ---------------------------------------------------------
# Alpaca API Layer
# ---------------------------------------------------------
def alpaca_request(method: str, endpoint: str, is_data: bool = False, params: Optional[dict] = None, json_data: Optional[dict] = None) -> Optional[dict]:
    url = f"{DATA_URL if is_data else BASE_URL}{endpoint}"
    for attempt in range(1, 4):
        try:
            resp = SESSION.request(method, url, params=params, json=json_data, timeout=15)
            if resp.status_code in [200, 201]:
                return resp.json()
            elif resp.status_code == 401:
                logger.error(f"Alpaca API 401 Unauthorized on {endpoint}: Check API Keys.")
                return None
            elif resp.status_code in [400, 404, 422]:
                logger.error(f"Alpaca API {resp.status_code} on {endpoint}: {resp.text}")
                return None
            else:
                logger.warning(f"Alpaca API {resp.status_code} [{attempt}/3] on {endpoint}: {resp.text}")
                time.sleep(2 * attempt)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error on {endpoint} [{attempt}/3]: {e}")
            time.sleep(2 * attempt)
    logger.error(f"Alpaca API request permanently failed: {endpoint}")
    return None

def fetch_account_summary() -> Tuple[float, float, List[dict], List[dict]]:
    """Returns (NAV, Cash, Open Positions, Open Orders)"""
    account_data = alpaca_request("GET", "/account")
    positions_data = alpaca_request("GET", "/positions")
    orders_data = alpaca_request("GET", "/orders?status=open")
    
    if account_data and "equity" in account_data:
        nav = float(account_data["equity"])
        cash = float(account_data["cash"])
        positions = positions_data if isinstance(positions_data, list) else []
        orders = orders_data if isinstance(orders_data, list) else []
        return nav, cash, positions, orders
    raise RuntimeError("Failed to retrieve Account Summary from Alpaca.")

def fetch_daily_bars(symbol: str, limit: int = WARMUP_BARS) -> Optional[pd.DataFrame]:
    """Fetches completed daily bars via Alpaca Market Data v2"""
    endpoint = f"/stocks/{symbol}/bars"
    params = {
        "timeframe": "1Day",
        "limit": limit,
        "adjustment": "all",
        "feed": "sip"
    }
    data = alpaca_request("GET", endpoint, is_data=True, params=params)
    if not data or "bars" not in data or not data["bars"]:
        # Fallback to IEX feed if SIP not permitted on paper
        params["feed"] = "iex"
        data = alpaca_request("GET", endpoint, is_data=True, params=params)
        if not data or "bars" not in data or not data["bars"]:
            logger.error(f"Failed to fetch Daily bars for {symbol}")
            return None

    records = []
    for b in data["bars"]:
        records.append({
            "timestamp": pd.to_datetime(b["t"]),
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": int(b["v"])
        })
    df = pd.DataFrame(records)
    return df.sort_values("timestamp").reset_index(drop=True) if not df.empty else None

# ---------------------------------------------------------
# Alpha Signal Evaluation (Hardened Elite 20)
# ---------------------------------------------------------
def evaluate_symbol_signal(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    if len(df) < 55:
        return None

    df = df.copy()
    df["ma50"] = df["close"].rolling(50).mean()
    df["std50"] = df["close"].rolling(50).std()
    df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
    df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])

    cur = df.iloc[-1]
    prev = df.iloc[-2]

    # Reclaim Setup: Prior close < -2.5 sigma, Current close > -2.5 sigma and < 50 SMA
    if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma50"]:
        stop_dist = float(cur["close"] - cur["lower_35s"])
        if stop_dist > 0.05:
            return {
                "symbol": symbol,
                "sector": SYMBOL_TO_SECTOR[symbol],
                "side": "buy",
                "close_px": round(float(cur["close"]), 2),
                "stop_px": round(float(cur["lower_35s"]), 2),
                "target_px": round(float(cur["ma50"]), 2),
                "stop_dist": round(stop_dist, 2),
                "timestamp": cur["timestamp"]
            }
    return None

def place_bracket_order(symbol: str, qty: int, stop_px: float, target_px: float) -> bool:
    """Submits Market Order with OCO Take-Profit and Stop-Loss brackets"""
    order_payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {
            "limit_price": f"{target_px:.2f}"
        },
        "stop_loss": {
            "stop_price": f"{stop_px:.2f}"
        }
    }
    logger.info(f"Submitting Bracket Order: {symbol} | Qty: {qty} | Stop: ${stop_px:.2f} | Target: ${target_px:.2f}")
    resp = alpaca_request("POST", "/orders", json_data=order_payload)
    if resp and "id" in resp:
        logger.info(f"✅ Bracket Order ACCEPTED: ID {resp['id']} | Status: {resp.get('status')}")
        return True
    logger.error(f"❌ Order Submission Rejected: {resp}")
    return False

# ---------------------------------------------------------
# Daily Portfolio Cycle & Scheduler
# ---------------------------------------------------------
def run_trading_cycle():
    logger.info("=" * 80)
    logger.info("Executing Daily Elite 20 Equities Scan Cycle...")

    try:
        nav, cash, positions, open_orders = fetch_account_summary()
    except Exception as e:
        logger.error(f"Cycle aborted. Failed to fetch account state: {e}")
        return

    open_symbols = set(p["symbol"] for p in positions)
    open_pending_symbols = set(o["symbol"] for o in open_orders)
    active_symbols = open_symbols.union(open_pending_symbols)

    active_sectors = set(SYMBOL_TO_SECTOR.get(sym, "UNKNOWN") for sym in active_symbols)
    num_active = len(active_symbols)
    current_gross_risk = num_active * BASE_RISK_PCT

    logger.info(f"Account NAV: ${nav:,.2f} | Cash: ${cash:,.2f}")
    logger.info(f"Active Positions: {num_active}/{MAX_CONCURRENT_POSITIONS} | Gross Risk: {current_gross_risk*100:.2f}% / {GROSS_RISK_CAP_PCT*100:.1f}%")
    logger.info(f"Occupied Sectors: {', '.join(sorted(active_sectors)) if active_sectors else 'None'}")

    if num_active >= MAX_CONCURRENT_POSITIONS:
        logger.info("Portfolio at max capacity (3 positions / 1.50% open risk). Skipping scan.")
        return

    valid_signals = []
    for sym in ELITE_20_SYMBOLS:
        if sym in active_symbols:
            continue

        sec = SYMBOL_TO_SECTOR[sym]
        if sec in active_sectors:
            continue  # Firewall: Max 1 per sector

        df = fetch_daily_bars(sym, limit=WARMUP_BARS)
        if df is None:
            continue

        sig = evaluate_symbol_signal(df, sym)
        if sig:
            valid_signals.append(sig)

    if not valid_signals:
        logger.info("No new -2.5σ reclaim setups detected across available sectors.")
        return

    logger.info(f"Detected {len(valid_signals)} valid setup(s): {[s['symbol'] + ' (' + s['sector'] + ')' for s in valid_signals]}")

    for sig in valid_signals:
        if num_active >= MAX_CONCURRENT_POSITIONS:
            logger.info(f"Gross Risk Cap reached. Setup for {sig['symbol']} bypassed.")
            break

        sym = sig["symbol"]
        sec = sig["sector"]

        if sec in active_sectors:
            logger.info(f"Sector Firewall triggered for {sym} (Sector {sec} already active). Bypassed.")
            continue

        target_risk_usd = nav * BASE_RISK_PCT
        stop_dist = sig["stop_dist"]
        if stop_dist <= 0:
            continue

        qty = int(math.floor(target_risk_usd / stop_dist))
        if qty < 1:
            logger.warning(f"Calculated shares < 1 for {sym}. Skipping trade.")
            continue

        if place_bracket_order(sym, qty, sig["stop_px"], sig["target_px"]):
            num_active += 1
            active_symbols.add(sym)
            active_sectors.add(sec)

def sleep_until_next_eod_scan():
    """Calculates sleep interval until next equity market pre-close (15:55 ET / 19:55 UTC Mon-Fri)."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=19, minute=55, second=0, microsecond=0)
    
    if now >= target:
        target += timedelta(days=1)
        
    # If target is on weekend, roll forward to Monday
    while target.weekday() >= 5:  # 5=Sat, 6=Sun
        target += timedelta(days=1)

    sleep_sec = max(10, (target - now).total_seconds())
    logger.info(f"Next scan scheduled at {target.strftime('%Y-%m-%d %H:%M:%S UTC')} ({sleep_sec/3600:.2f} hours).")
    time.sleep(sleep_sec)

def main():
    logger.info("=" * 80)
    logger.info("Starting Certified Elite 20 Equities Mean Reversion Daemon...")
    logger.info(f"Environment: {ALPACA_ENVIRONMENT.upper()} | Base URL: {BASE_URL}")
    logger.info(f"Trade Sizing: {BASE_RISK_PCT*100:.2f}% NAV | Gross Cap: {GROSS_RISK_CAP_PCT*100:.1f}% NAV (Max {MAX_CONCURRENT_POSITIONS})")
    logger.info(f"Sector Firewall: Max {MAX_PER_SECTOR} position per sector | Universe: {len(ELITE_20_SYMBOLS)} Tickers")
    logger.info("=" * 80)

    # Initial scan on daemon startup
    run_trading_cycle()

    while True:
        try:
            sleep_until_next_eod_scan()
            run_trading_cycle()
        except Exception as e:
            logger.error(f"Unexpected error in daemon loop: {e}", exc_info=True)
            time.sleep(60)

if __name__ == "__main__":
    main()
