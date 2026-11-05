#!/usr/bin/env python3
"""
Production Live/Paper Trading Bot: H4 Extreme-Tail Mean Reversion (E4 Baseline)
Architecture:
- Timeframe: Native H4 (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)
- Baseline: 100 SMA
- Entry: Close reclaim of +/-2.5-Sigma envelope
- Stop Loss: Point-in-time frozen +/-3.5-Sigma envelope (minimum 6.0 pips)
- Take Profit: Point-in-time frozen 100 SMA centerline (T1 Geometry)
- Sizing: Dynamic 0.50% NAV risk per trade
- Portfolio Budget: Gross 1.50% NAV risk cap (Max 3 concurrent positions)
- Universe: 9 Pairs (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, NZD_USD, USD_CHF, EUR_JPY, GBP_JPY)
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
LOG_FILE = LOG_DIR / "live_h4_mean_reversion.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("H4_LiveBot")

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

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice").lower()

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    logger.error("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID in environment / .env")
    sys.exit(1)

API_HOST = "api-fxpractice.oanda.com" if OANDA_ENVIRONMENT == "practice" else "api-fxtrade.oanda.com"
BASE_URL = f"https://{API_HOST}/v3"

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json",
    "Accept-Datetime-Format": "RFC3339"
})

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]
RISK_PER_TRADE_PCT = 0.0050   # 0.50% NAV
GROSS_RISK_CAP_PCT = 0.0150   # 1.50% Gross Open Portfolio Risk
MAX_CONCURRENT_POSITIONS = 3
MIN_STOP_PIPS = 6.0
WARMUP_BARS = 120

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def get_price_precision(symbol: str) -> int:
    return 3 if "JPY" in symbol else 5

# ---------------------------------------------------------
# OANDA API Layer
# ---------------------------------------------------------
def oanda_request(method: str, endpoint: str, params: Optional[dict] = None, json_data: Optional[dict] = None) -> Optional[dict]:
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(1, 4):
        try:
            resp = SESSION.request(method, url, params=params, json=json_data, timeout=15)
            if resp.status_code in [200, 201]:
                return resp.json()
            elif resp.status_code == 401:
                logger.error(f"API Error 401 Unauthorized on {endpoint}: Check OANDA_API_KEY.")
                return None
            elif resp.status_code in [400, 404]:
                logger.error(f"API Error {resp.status_code} on {endpoint}: {resp.text}")
                return None
            else:
                logger.warning(f"OANDA API {resp.status_code} [{attempt}/3] on {endpoint}: {resp.text}")
                time.sleep(2 * attempt)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error on {endpoint} [{attempt}/3]: {e}")
            time.sleep(2 * attempt)
    logger.error(f"OANDA API request permanently failed: {endpoint}")
    return None

def fetch_account_summary() -> Tuple[float, float, List[dict]]:
    """Returns (NAV, Balance, Open Trades List)"""
    data = oanda_request("GET", f"/accounts/{OANDA_ACCOUNT_ID}/summary")
    trades_data = oanda_request("GET", f"/accounts/{OANDA_ACCOUNT_ID}/openTrades")
    
    if data and "account" in data:
        nav = float(data["account"]["NAV"])
        balance = float(data["account"]["balance"])
        open_trades = trades_data.get("trades", []) if trades_data else []
        return nav, balance, open_trades
    raise RuntimeError("Failed to retrieve Account Summary from OANDA.")

def fetch_h4_candles(symbol: str, count: int = WARMUP_BARS) -> Optional[pd.DataFrame]:
    endpoint = f"/instruments/{symbol}/candles"
    params = {"price": "M", "granularity": "H4", "count": count}
    data = oanda_request("GET", endpoint, params=params)
    if not data or "candles" not in data:
        logger.error(f"Failed to fetch H4 candles for {symbol}")
        return None
    
    records = []
    for c in data["candles"]:
        if not c["complete"]:
            continue  # Finalized candles only
        records.append({
            "time": pd.to_datetime(c["time"], utc=True),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"])
        })
    df = pd.DataFrame(records)
    return df.sort_values("time").reset_index(drop=True) if not df.empty else None

def get_quote_to_usd_rate(symbol: str, candle_cache: Dict[str, pd.DataFrame]) -> float:
    """Calculates pip conversion rate to USD for exact sizing."""
    base, quote = symbol.split("_")
    if quote == "USD":
        return 1.0
    if base == "USD":
        px = candle_cache[symbol]["close"].iloc[-1]
        return 1.0 / px if px > 0 else 1.0
    if quote == "JPY":
        uj_px = candle_cache.get("USD_JPY", fetch_h4_candles("USD_JPY", 5))["close"].iloc[-1]
        return 1.0 / uj_px if uj_px > 0 else 1.0 / 150.0
    if quote == "CHF":
        uchf_px = candle_cache.get("USD_CHF", fetch_h4_candles("USD_CHF", 5))["close"].iloc[-1]
        return 1.0 / uchf_px if uchf_px > 0 else 1.0
    if quote == "CAD":
        ucad_px = candle_cache.get("USD_CAD", fetch_h4_candles("USD_CAD", 5))["close"].iloc[-1]
        return 1.0 / ucad_px if ucad_px > 0 else 1.0
    return 1.0

# ---------------------------------------------------------
# Alpha & Execution Logic (E4 Frozen)
# ---------------------------------------------------------
def evaluate_symbol_signal(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    if len(df) < 102:
        return None
        
    df = df.copy()
    pip_val = get_pip_value(symbol)
    
    df["ma100"] = df["close"].rolling(100).mean()
    df["std100"] = df["close"].rolling(100).std()
    df["upper_25s"] = df["ma100"] + (2.5 * df["std100"])
    df["lower_25s"] = df["ma100"] - (2.5 * df["std100"])
    df["upper_35s"] = df["ma100"] + (3.5 * df["std100"])
    df["lower_35s"] = df["ma100"] - (3.5 * df["std100"])
    
    cur = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Long Reclaim Setup
    if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma100"]:
        stop_dist = cur["close"] - cur["lower_35s"]
        stop_pips = max(MIN_STOP_PIPS, stop_dist / pip_val)
        return {
            "symbol": symbol,
            "side": "BUY",
            "close_px": float(cur["close"]),
            "stop_pips": round(stop_pips, 1),
            "stop_px": float(cur["close"] - (stop_pips * pip_val)),
            "target_px": float(cur["ma100"]),
            "time": cur["time"]
        }
        
    # Short Reclaim Setup
    elif prev["close"] > prev["upper_25s"] and cur["close"] < cur["upper_25s"] and cur["close"] > cur["ma100"]:
        stop_dist = cur["upper_35s"] - cur["close"]
        stop_pips = max(MIN_STOP_PIPS, stop_dist / pip_val)
        return {
            "symbol": symbol,
            "side": "SELL",
            "close_px": float(cur["close"]),
            "stop_pips": round(stop_pips, 1),
            "stop_px": float(cur["close"] + (stop_pips * pip_val)),
            "target_px": float(cur["ma100"]),
            "time": cur["time"]
        }
        
    return None

def place_market_order_with_brackets(symbol: str, units: int, stop_px: float, target_px: float) -> bool:
    prec = get_price_precision(symbol)
    stop_str = f"{stop_px:.{prec}f}"
    target_str = f"{target_px:.{prec}f}"
    
    order_payload = {
        "order": {
            "type": "MARKET",
            "instrument": symbol,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": stop_str, "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": target_str, "timeInForce": "GTC"}
        }
    }
    
    logger.info(f"Submitting Order: {symbol} | Units: {units:+d} | SL: {stop_str} | TP: {target_str}")
    endpoint = f"/accounts/{OANDA_ACCOUNT_ID}/orders"
    resp = oanda_request("POST", endpoint, json_data=order_payload)
    
    if resp and "orderFillTransaction" in resp:
        fill = resp["orderFillTransaction"]
        logger.info(f"✅ Order FILLED: ID {fill['id']} | {symbol} @ {fill['price']} | Units: {fill['units']}")
        return True
    elif resp and "orderCancelTransaction" in resp:
        logger.warning(f"❌ Order CANCELLED: {resp['orderCancelTransaction'].get('reason')}")
        return False
    else:
        logger.error(f"❌ Order Execution Failed: {resp}")
        return False

# ---------------------------------------------------------
# Portfolio Cycle & Scheduling
# ---------------------------------------------------------
def run_trading_cycle():
    logger.info("=" * 75)
    logger.info("Executing H4 Bar Check Cycle...")
    
    try:
        nav, balance, open_trades = fetch_account_summary()
    except Exception as e:
        logger.error(f"Cycle aborted. Failed to fetch account state: {e}")
        return
        
    num_open_positions = len(open_trades)
    open_symbols = set(t["instrument"] for t in open_trades)
    current_gross_risk = num_open_positions * RISK_PER_TRADE_PCT
    
    logger.info(f"Account NAV: ${nav:,.2f} | Balance: ${balance:,.2f}")
    logger.info(f"Open Positions: {num_open_positions}/{MAX_CONCURRENT_POSITIONS} | Gross Risk: {current_gross_risk*100:.2f}% / {GROSS_RISK_CAP_PCT*100:.1f}%")
    
    if num_open_positions >= MAX_CONCURRENT_POSITIONS:
        logger.info("Portfolio at maximum capacity (3 positions). Skipping scan.")
        return

    candle_cache = {}
    valid_signals = []
    
    for sym in sorted(PAIRS):
        if sym in open_symbols:
            continue
            
        df = fetch_h4_candles(sym, count=WARMUP_BARS)
        if df is None:
            continue
        candle_cache[sym] = df
        
        sig = evaluate_symbol_signal(df, sym)
        if sig:
            valid_signals.append(sig)
            
    if not valid_signals:
        logger.info("No new H4 mean reversion setups detected.")
        return
        
    logger.info(f"Detected {len(valid_signals)} setup(s): {[s['symbol'] + '_' + s['side'] for s in valid_signals]}")
    
    # Admit setups up to Gross 1.5% Cap
    for sig in valid_signals:
        if num_open_positions >= MAX_CONCURRENT_POSITIONS:
            logger.info(f"Risk Cap reached. Setup for {sig['symbol']} bypassed.")
            break
            
        sym = sig["symbol"]
        side = sig["side"]
        stop_pips = sig["stop_pips"]
        pip_val = get_pip_value(sym)
        quote_to_usd = get_quote_to_usd_rate(sym, candle_cache)
        
        risk_per_unit = (stop_pips * pip_val) * quote_to_usd
        target_risk_usd = nav * RISK_PER_TRADE_PCT
        
        if risk_per_unit <= 0:
            continue
            
        units = int(math.floor(target_risk_usd / risk_per_unit))
        signed_units = units if side == "BUY" else -units
        
        if abs(signed_units) < 1:
            logger.warning(f"Calculated units < 1 for {sym}. Skipping trade.")
            continue
            
        if place_market_order_with_brackets(sym, signed_units, sig["stop_px"], sig["target_px"]):
            num_open_positions += 1
            open_symbols.add(sym)

def sleep_until_next_h4_boundary():
    """Calculates sleep time until the next H4 candle close + 15 sec buffer."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    next_hour = (current_hour // 4 + 1) * 4
    
    target_time = now.replace(minute=0, second=15, microsecond=0)
    if next_hour == 24:
        target_time = target_time + timedelta(days=1)
        target_time = target_time.replace(hour=0)
    else:
        target_time = target_time.replace(hour=next_hour)
        
    sleep_seconds = max(5, (target_time - now).total_seconds())
    logger.info(f"Next H4 bar check scheduled at {target_time.strftime('%Y-%m-%d %H:%M:%S UTC')} (in {sleep_seconds/60:.1f} mins).")
    time.sleep(sleep_seconds)

def main():
    logger.info("Starting Optimized H4 Mean Reversion Daemon...")
    logger.info(f"Environment: {OANDA_ENVIRONMENT.upper()} | Account ID: {OANDA_ACCOUNT_ID}")
    logger.info(f"Trade Sizing: {RISK_PER_TRADE_PCT*100:.2f}% NAV | Gross Cap: {GROSS_RISK_CAP_PCT*100:.1f}% NAV (Max 3)")
    logger.info(f"Universe ({len(PAIRS)} pairs): {', '.join(PAIRS)}")
    
    # Run immediate startup cycle
    run_trading_cycle()
    
    while True:
        try:
            sleep_until_next_h4_boundary()
            run_trading_cycle()
        except Exception as e:
            logger.error(f"Unexpected error in daemon loop: {e}", exc_info=True)
            time.sleep(60)

if __name__ == "__main__":
    main()
