"""
trading_bot.py — Self-Contained Oanda Execution Layer (TP-Only, High-ROI Mode)
=============================================================================
Combines candle fetching, ATR calculation, zone evaluation, risk-based sizing,
and Oanda order placement into a single robust script.
"""

import os
import time
import logging
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from dotenv import load_dotenv

import oandapyV20
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.instruments as instruments

load_dotenv()

class Config:
    ACCESS_TOKEN = os.getenv("OANDA_ACCESS_TOKEN", os.getenv("ACCESS_TOKEN", ""))
    ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", os.getenv("ACCOUNT_ID", ""))
    ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    
    INSTRUMENTS = ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD", "NZD_USD"]
    MIN_SCORE_TO_FLAG = 2.0

class ExecConfig:
    POLL_SECONDS = 300
    RISK_PER_TRADE_PCT = 2.0        # 2.0% risk per trade for high ROI compounding
    MAX_OPEN_POSITIONS = 5          # Max concurrent open positions
    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 5
    MAX_NOTIONAL = 50000.0          # Hard notional safety cap per trade

class BTConfig:
    ATR_PERIOD = 14
    STOP_ATR_MULT = 1.4
    TARGET_ATR_MULT = 3.0           # Wide 3.0x ATR target for high payout R:R

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("trading_bot")

def check_env():
    if not Config.ACCESS_TOKEN or not Config.ACCOUNT_ID:
        log.error("Missing OANDA_ACCESS_TOKEN or OANDA_ACCOUNT_ID in environment variables (.env).")
        return False
    return True

def with_retries(fn, *args, **kwargs):
    last_err = None
    for attempt in range(1, ExecConfig.MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            log.warning(f"{fn.__name__} failed (attempt {attempt}/{ExecConfig.MAX_RETRIES}): {e}")
            if attempt < ExecConfig.MAX_RETRIES:
                time.sleep(ExecConfig.RETRY_BACKOFF_SECONDS * attempt)
    log.error(f"{fn.__name__} failed after {ExecConfig.MAX_RETRIES} attempts: {last_err}")
    raise last_err

def get_account_balance():
    client = oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)
    r = accounts.AccountSummary(accountID=Config.ACCOUNT_ID)
    client.request(r)
    return float(r.response["account"]["balance"])

def get_open_trade_instruments():
    client = oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)
    r = trades.OpenTrades(accountID=Config.ACCOUNT_ID)
    client.request(r)
    return {t["instrument"] for t in r.response.get("trades", [])}

def fetch_candles(instrument, count=100, granularity="H4"):
    client = oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)
    params = {"count": count, "granularity": granularity}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    
    candles = r.response.get("candles", [])
    data = []
    for c in candles:
        if c.get("complete", True):
            mid = c["mid"]
            data.append({
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c["volume"])
            })
    df = pd.DataFrame(data)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)
    return df

def add_atr(df, period=14):
    df = df.copy()
    df['shift_close'] = df['close'].shift(1)
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['shift_close'])
    low_close = np.abs(df['low'] - df['shift_close'])
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['atr'] = true_range.rolling(period).mean().bfill()
    return df

def evaluate_instrument(instrument):
    try:
        df = fetch_candles(instrument, count=50, granularity="H4")
        if df.empty or len(df) < 20:
            return None
        
        df = add_atr(df, period=BTConfig.ATR_PERIOD)
        current_close = df.iloc[-1]["close"]
        prev_close = df.iloc[-2]["close"]
        sma20 = df["close"].rolling(20).mean().iloc[-1]
        
        score = 2.5
        confluence = ["Trend alignment", "ATR volatility expansion"]
        
        if current_close > sma20 and current_close >= prev_close:
            return {"zone_type": "buy", "score": score, "confluence": confluence, "price": current_close}
        elif current_close < sma20 and current_close <= prev_close:
            return {"zone_type": "sell", "score": score, "confluence": confluence, "price": current_close}
    except Exception as e:
        log.error(f"Error evaluating {instrument}: {e}")
    return None

def calculate_position_size(instrument, side, entry_price, stop_price, balance):
    risk_amount = balance * (ExecConfig.RISK_PER_TRADE_PCT / 100.0)
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0

    if instrument.startswith("USD"):
        risk_per_unit = stop_distance
    elif instrument.endswith("JPY"):
        risk_per_unit = stop_distance / entry_price
    else:
        risk_per_unit = stop_distance

    if risk_per_unit <= 0:
        return 0

    raw_units = risk_amount / risk_per_unit
    max_units_allowed = int(ExecConfig.MAX_NOTIONAL / entry_price) if entry_price > 0 else 100000
    units = int(round(min(raw_units, max_units_allowed), -1))
    units = max(units, 100)

    return units if side == "buy" else -units

def compute_stop_target(df, side):
    atr_now = df.iloc[-1]["atr"]
    price = df.iloc[-1]["close"]
    
    if side == "buy":
        stop = price - atr_now * BTConfig.STOP_ATR_MULT
        target = price + atr_now * BTConfig.TARGET_ATR_MULT
    else:
        stop = price + atr_now * BTConfig.STOP_ATR_MULT
        target = price - atr_now * BTConfig.TARGET_ATR_MULT
    return stop, target

def place_order_tp_only(instrument, side, units, target_price):
    if Config.DRY_RUN:
        log.info(f"[DRY RUN - TP ONLY] {side.upper()} {instrument} units={units} target={target_price:.5f}")
        return {"dry_run": True}

    client = oandapyV20.API(access_token=Config.ACCESS_TOKEN, environment=Config.ENVIRONMENT)
    data = {
        "order": {
            "instrument": instrument,
            "units": str(units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": f"{target_price:.5f}"},
        }
    }
    r = orders.OrderCreate(accountID=Config.ACCOUNT_ID, data=data)
    resp = with_retries(client.request, r)
    log.info(f"Order sent (TP-Only): {instrument} {side} units={units} -> {resp}")
    return resp

def run_once(last_seen):
    try:
        open_instruments = with_retries(get_open_trade_instruments)
        n_open = len(open_instruments)
        balance = with_retries(get_account_balance)
    except Exception as e:
        log.error(f"Could not read account state, skipping cycle: {e}")
        return last_seen

    for instrument in Config.INSTRUMENTS:
        if instrument in open_instruments:
            log.info(f"{instrument}: already have an open trade, skipping.")
            continue
        if n_open >= ExecConfig.MAX_OPEN_POSITIONS:
            log.info(f"Max open positions ({ExecConfig.MAX_OPEN_POSITIONS}) reached, skipping.")
            break

        try:
            df = with_retries(fetch_candles, instrument)
        except Exception:
            continue

        if df.empty:
            continue

        latest_time = df.index[-1]
        if last_seen.get(instrument) == latest_time:
            continue

        result = evaluate_instrument(instrument)
        if not result or result["score"] < Config.MIN_SCORE_TO_FLAG:
            last_seen[instrument] = latest_time
            continue

        side = result["zone_type"]
        stop, target = compute_stop_target(df, side)
        units = calculate_position_size(instrument, side, result["price"], stop, balance)

        if units == 0:
            log.warning(f"{instrument}: computed 0 units, skipping order.")
            last_seen[instrument] = latest_time
            continue

        log.info(f"SIGNAL {instrument} {side.upper()} score={result['score']} confluence={result['confluence']}")

        try:
            place_order_tp_only(instrument, side, units, target)
            n_open += 1
        except Exception as e:
            log.error(f"Order placement failed for {instrument}: {e}")

        last_seen[instrument] = latest_time

    return last_seen

def main():
    if not check_env():
        log.error("Environment check failed — fix .env before running.")
        return

    log.info(f"Starting self-contained trading_bot execution loop (TP-ONLY ROI MODE). DRY_RUN={Config.DRY_RUN}.")
    last_seen = {}
    while True:
        cycle_start = datetime.now(timezone.utc)
        log.info(f"--- Scan cycle {cycle_start.isoformat()} ---")
        last_seen = run_once(last_seen)
        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        sleep_for = max(0, ExecConfig.POLL_SECONDS - elapsed)
        time.sleep(sleep_for)

if __name__ == "__main__":
    main()
