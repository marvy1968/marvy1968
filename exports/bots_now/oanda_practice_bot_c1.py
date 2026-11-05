from pathlib import Path

# --- ENHANCEMENTS: CIRCUIT BREAKER, LEDGER, & ALERTS ---
import csv
from datetime import date

LEDGER_FILE = Path("/home/moonrider1968/bots/trade_ledger.csv")
DAILY_START_NAV = None
LAST_RESET_DATE = None
CIRCUIT_BREAKER_TRIPPED = False

def log_trade_to_ledger(trade_dict: dict):
    file_exists = LEDGER_FILE.exists()
    with open(LEDGER_FILE, mode="a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "pair", "side", "entry_px", "exit_px", "pnl", "r_multiple"])
        if not file_exists:
            writer.writeheader()
        writer.writerow(trade_dict)

def check_daily_circuit_breaker(current_nav: float) -> bool:
    global DAILY_START_NAV, LAST_RESET_DATE, CIRCUIT_BREAKER_TRIPPED
    today = date.today()
    
    if LAST_RESET_DATE != today:
        DAILY_START_NAV = current_nav
        LAST_RESET_DATE = today
        CIRCUIT_BREAKER_TRIPPED = False
        
    if DAILY_START_NAV and current_nav < DAILY_START_NAV * (1 - 0.02):
        CIRCUIT_BREAKER_TRIPPED = True
        print("[!] CIRCUIT BREAKER TRIPPED: Account down >= 2.0% today. Halting new trades.")
        return True
        
    return CIRCUIT_BREAKER_TRIPPED


RISK_PCT = 0.01
#!/usr/bin/env python3
"""
OANDA PRACTICE BOT — FINALIST C1 (2+1 ARCHITECTURE)
Exact Parity Engine:
  - F0: 100 SMA, +-2.5 sigma reclaim entry, +-3.5 sigma stop, centerline target.
  - Donchian: 20-period High/Low breakout gated strictly by Wilder ADX >= 25.0.
  - Governance: Max 3 positions, Donchian <= 1, F0 <= 3, Factor Firewall <= 2.
  - Sizing: 0.50% NAV per trade with full cross-currency quote-to-USD conversion.
"""

import os
import sys
import time
import json
import logging
import datetime as dt
from typing import Dict, List, Tuple, Optional
import requests
import numpy as np
import pandas as pd

# ------------------------------------------------------------------------------
# 1. ENVIRONMENT & CONFIGURATION
# ------------------------------------------------------------------------------
def load_env_file(filepath="/home/moonrider1968/bots/.env"):
    p = Path(filepath)
    if not p.exists():
        return
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v

load_env_file()

OANDA_API_KEY = os.environ.get("OANDA_API_TOKEN") or os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
OANDA_URL = os.environ.get("OANDA_URL", "https://api-fxpractice.oanda.com")

PAIRS = ["USD_CAD", "USD_CHF", "USD_JPY", "EUR_JPY"]

TIMEFRAME = "H4"
RISK_PER_TRADE_PCT = 0.0050
MAX_PORTFOLIO_POSITIONS = 3
MAX_DONCHIAN_POSITIONS = 1
MAX_F0_POSITIONS = 3
MAX_FACTOR_EXPOSURE = 2
POLL_INTERVAL_SECONDS = 60

LOG_FILE = "/home/moonrider1968/bots/oanda_c1_practice.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("C1_BOT")

# ------------------------------------------------------------------------------
# 2. OANDA API CLIENT
# ------------------------------------------------------------------------------
class OandaClient:
    def __init__(self, api_key: str, account_id: str, base_url: str):
        self.api_key = api_key
        self.account_id = account_id
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339"
        }

    def get_account_summary(self) -> dict:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/summary"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("account", {})

    def get_candles(self, instrument: str, granularity: str = "H4", count: int = 200) -> pd.DataFrame:
        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {"granularity": granularity, "count": count, "price": "M"}
        resp = requests.get(url, headers=self.headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("candles", [])
        
        records = []
        for c in data:
            if not c["complete"]:
                continue
            records.append({
                "time": pd.to_datetime(c["time"], utc=True),
                "open": float(c["mid"]["o"]),
                "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]),
                "close": float(c["mid"]["c"]),
                "volume": int(c["volume"])
            })
        return pd.DataFrame(records).sort_values("time").reset_index(drop=True)

    def get_open_trades(self) -> List[dict]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/openTrades"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("trades", [])

    def get_current_pricing(self, instruments: List[str]) -> Dict[str, dict]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/pricing"
        params = {"instruments": ",".join(instruments)}
        resp = requests.get(url, headers=self.headers, params=params, timeout=10)
        resp.raise_for_status()
        prices = {}
        for p in resp.json().get("prices", []):
            prices[p["instrument"]] = {
                "bid": float(p["bids"][0]["price"]),
                "ask": float(p["asks"][0]["price"]),
                "spread": float(p["asks"][0]["price"]) - float(p["bids"][0]["price"])
            }
        return prices

    def create_market_order(self, instrument: str, units: int, stop_loss: float, take_profit: float, client_tag: str) -> dict:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/orders"
        order_spec = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {
                    "timeInForce": "GTC",
                    "price": f"{stop_loss:.5f}" if "JPY" not in instrument else f"{stop_loss:.3f}"
                },
                "takeProfitOnFill": {
                    "timeInForce": "GTC",
                    "price": f"{take_profit:.5f}" if "JPY" not in instrument else f"{take_profit:.3f}"
                },
                "clientExtensions": {
                    "tag": client_tag,
                    "comment": f"C1_{client_tag}"
                },
                "tradeClientExtensions": {
                    "tag": client_tag,
                    "comment": f"C1_{client_tag}"
                }
            }
        }
        resp = requests.post(url, headers=self.headers, json=order_spec, timeout=10)
        return resp.json()

# ------------------------------------------------------------------------------
# 3. TECHNICAL INDICATORS & MATH
# ------------------------------------------------------------------------------
def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def parse_factors(symbol: str) -> Tuple[str, str]:
    base, quote = symbol.split("_")
    return base, quote

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

def calc_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = calc_atr(df, period)
    plus_di = 100.0 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100.0 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)

    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx, atr

# ------------------------------------------------------------------------------
# 4. CERTIFIED SIGNAL GENERATION ENGINES
# ------------------------------------------------------------------------------
def evaluate_f0_signal(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """
    Certified F0 Trend Engine:
      Baseline: 100-period SMA
      Bands: 100 SMA +- 2.5 sigma
      Stops: 100 SMA +- 3.5 sigma
      Target: Centerline (100 SMA)
      Entry Trigger: Close reclaims inside the 2.5 sigma band after piercing it.
      Special Governance: USD_CAD & USD_CHF require 200 SMA macro trend alignment
                          (BUY only when > 200 SMA, SELL only when < 200 SMA).
    """
    if len(df) < 110:
        return None

    pip_val = get_pip_value(symbol)
    close = df["close"]
    sma100 = close.rolling(100).mean()
    std100 = close.rolling(100).std()

    curr_idx = len(df) - 1
    c_prev = close.iloc[curr_idx - 1]
    c_curr = close.iloc[curr_idx]

    mean_curr = sma100.iloc[curr_idx]
    sigma_curr = std100.iloc[curr_idx]
    if pd.isna(mean_curr) or pd.isna(sigma_curr) or sigma_curr <= 0:
        return None

    # Macro 200 SMA trend check for drift-heavy pairs
    trend_filter_pairs = ["USD_CAD", "USD_CHF"]
    sma200_curr = None
    if symbol in trend_filter_pairs:
        if len(df) < 205:
            return None
        sma200 = close.rolling(200).mean()
        sma200_curr = sma200.iloc[curr_idx]
        if pd.isna(sma200_curr):
            return None

    upper_25 = mean_curr + (2.5 * sigma_curr)
    lower_25 = mean_curr - (2.5 * sigma_curr)
    upper_35 = mean_curr + (3.5 * sigma_curr)
    lower_35 = mean_curr - (3.5 * sigma_curr)

    # Bullish Reclaim: Pierced below -2.5 sigma, now closed back above it
    if c_prev < lower_25 and c_curr >= lower_25 and c_curr < mean_curr:
        if sma200_curr is not None and c_curr <= sma200_curr:
            return None  # USD_CAD/USD_CHF macro downtrend filter
        stop_px = lower_35
        target_px = mean_curr
        stop_dist = c_curr - stop_px
        if stop_dist > (15 * pip_val):
            return {
                "strategy": "F0",
                "priority": 1,
                "pair": symbol,
                "side": "BUY",
                "stop_px": stop_px,
                "target_px": target_px,
                "stop_dist": stop_dist
            }

    # Bearish Reclaim: Pierced above +2.5 sigma, now closed back below it
    elif c_prev > upper_25 and c_curr <= upper_25 and c_curr > mean_curr:
        if sma200_curr is not None and c_curr >= sma200_curr:
            return None  # USD_CAD/USD_CHF macro uptrend filter
        stop_px = upper_35
        target_px = mean_curr
        stop_dist = stop_px - c_curr
        if stop_dist > (15 * pip_val):
            return {
                "strategy": "F0",
                "priority": 1,
                "pair": symbol,
                "side": "SELL",
                "stop_px": stop_px,
                "target_px": target_px,
                "stop_dist": stop_dist
            }

    return None

def evaluate_donchian_gated_signal(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """
    Certified Gated Donchian Engine:
      Channel: 20-period High/Low (shifted 1 bar)
      Regime Gate: ADX_14 >= 25.0
      Stop: 2.0 * ATR_14 (min 20 pips)
      Target: 3.0 * Stop Distance (1:3 R:R)
    """
    if len(df) < 35:
        return None

    pip_val = get_pip_value(symbol)
    adx, atr = calc_adx(df, 14)
    upper_band = df["high"].shift(1).rolling(20).max()
    lower_band = df["low"].shift(1).rolling(20).min()

    curr_idx = len(df) - 1
    curr_adx = adx.iloc[curr_idx]
    if pd.isna(curr_adx) or curr_adx < 25.0:
        return None

    c = df["close"].iloc[curr_idx]
    curr_atr = atr.iloc[curr_idx]
    u_band = upper_band.iloc[curr_idx]
    l_band = lower_band.iloc[curr_idx]

    if pd.isna(u_band) or pd.isna(l_band) or pd.isna(curr_atr):
        return None

    stop_dist = max(20.0 * pip_val, 2.0 * curr_atr)

    if c > u_band:
        return {
            "strategy": "DONCHIAN_GATED",
            "priority": 2,
            "pair": symbol,
            "side": "BUY",
            "stop_px": c - stop_dist,
            "target_px": c + (3.0 * stop_dist),
            "stop_dist": stop_dist
        }
    elif c < l_band:
        return {
            "strategy": "DONCHIAN_GATED",
            "priority": 2,
            "pair": symbol,
            "side": "SELL",
            "stop_px": c + stop_dist,
            "target_px": c - (3.0 * stop_dist),
            "stop_dist": stop_dist
        }

    return None

# ------------------------------------------------------------------------------
# 5. GOVERNANCE: 2+1 ARCHITECTURE & FACTOR FIREWALL
# ------------------------------------------------------------------------------
def count_factor_exposure(open_pairs: List[str]) -> Dict[str, int]:
    factor_counts: Dict[str, int] = {}
    for p in open_pairs:
        b, q = parse_factors(p)
        factor_counts[b] = factor_counts.get(b, 0) + 1
        factor_counts[q] = factor_counts.get(q, 0) + 1
    return factor_counts

def passes_governance(signal: dict, open_trades: List[dict]) -> Tuple[bool, str]:
    pair = signal["pair"]
    strategy = signal["strategy"]

    for t in open_trades:
        if t["instrument"] == pair:
            return False, f"Pair {pair} already active"

    if len(open_trades) >= MAX_PORTFOLIO_POSITIONS:
        return False, f"Portfolio capacity reached ({len(open_trades)}/{MAX_PORTFOLIO_POSITIONS})"

    donchian_count = sum(1 for t in open_trades if "DONCHIAN" in t.get("clientExtensions", {}).get("tag", ""))
    f0_count = sum(1 for t in open_trades if "F0" in t.get("clientExtensions", {}).get("tag", ""))

    if strategy == "DONCHIAN_GATED" and donchian_count >= MAX_DONCHIAN_POSITIONS:
        return False, f"Donchian slot occupied ({donchian_count}/{MAX_DONCHIAN_POSITIONS})"
    if strategy == "F0" and f0_count >= MAX_F0_POSITIONS:
        return False, f"F0 slots occupied ({f0_count}/{MAX_F0_POSITIONS})"

    open_pairs = [t["instrument"] for t in open_trades]
    factor_counts = count_factor_exposure(open_pairs)
    cand_base, cand_quote = parse_factors(pair)

    if factor_counts.get(cand_base, 0) >= MAX_FACTOR_EXPOSURE:
        return False, f"Firewall: {cand_base} factor capped ({factor_counts[cand_base]})"
    if factor_counts.get(cand_quote, 0) >= MAX_FACTOR_EXPOSURE:
        return False, f"Firewall: {cand_quote} factor capped ({factor_counts[cand_quote]})"

    return True, "PASS"

# ------------------------------------------------------------------------------
# 6. SIZING & ACCURATE FX CONVERSION
# ------------------------------------------------------------------------------
def calculate_position_units(
    nav: float, 
    risk_pct: float, 
    pair: str, 
    stop_dist_quote: float, 
    pricing: Dict[str, dict]
) -> int:
    target_dollar_risk = nav * risk_pct
    base, quote = parse_factors(pair)

    # Resolve Quote-to-USD conversion rate accurately
    if quote == "USD":
        quote_to_usd = 1.0
    elif f"{quote}_USD" in pricing:
        quote_to_usd = pricing[f"{quote}_USD"]["bid"]
    elif f"USD_{quote}" in pricing:
        usd_quote = pricing[f"USD_{quote}"]["ask"]
        quote_to_usd = (1.0 / usd_quote) if usd_quote > 0 else 0.0
    else:
        logger.error(f"Cannot resolve Quote-to-USD conversion rate for quote currency: {quote}")
        return 0

    dollar_risk_per_unit = stop_dist_quote * quote_to_usd
    if dollar_risk_per_unit <= 0:
        return 0

    return int(target_dollar_risk / dollar_risk_per_unit)

# ------------------------------------------------------------------------------
# 7. MAIN BOT LOOP
# ------------------------------------------------------------------------------
def run_practice_bot():
    if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
        logger.error("Missing OANDA API credentials in environment or .env file.")
        sys.exit(1)

    client = OandaClient(OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_URL)
    logger.info("Initializing Finalist C1 Practice Execution Engine...")
    logger.info(f"Target Account: {OANDA_ACCOUNT_ID} | Universe: {len(PAIRS)} pairs | Risk/Trade: {RISK_PER_TRADE_PCT*100:.2f}%")

    last_evaluated_bar_time: Dict[str, dt.datetime] = {}

    while True:
        try:
            account = client.get_account_summary()
            nav = float(account.get("NAV", 100000.0))
            open_trades = client.get_open_trades()
            pricing = client.get_current_pricing(PAIRS)

            candidate_signals: List[dict] = []
            for pair in PAIRS:
                df = client.get_candles(pair, granularity=TIMEFRAME, count=220)
                if df.empty:
                    continue

                latest_bar_time = df["time"].iloc[-1]
                if last_evaluated_bar_time.get(pair) == latest_bar_time:
                    continue

                sig_f0 = evaluate_f0_signal(df, pair)
                sig_don = evaluate_donchian_gated_signal(df, pair)

                if sig_f0:
                    candidate_signals.append(sig_f0)
                if sig_don:
                    candidate_signals.append(sig_don)

                last_evaluated_bar_time[pair] = latest_bar_time

            candidate_signals.sort(key=lambda s: (s["priority"], s["pair"]))

            for sig in candidate_signals:
                passed, reason = passes_governance(sig, open_trades)
                pair = sig["pair"]
                strategy = sig["strategy"]

                if not passed:
                    logger.info(f"REJECT [{strategy} on {pair}]: {reason}")
                    continue

                stop_dist = sig["stop_dist"]
                units = calculate_position_units(nav, RISK_PER_TRADE_PCT, pair, stop_dist, pricing)

                if units <= 0:
                    logger.warning(f"Sizing computed 0 units for {pair}. Skipping.")
                    continue

                order_units = units if sig["side"] == "BUY" else -units
                tag = f"{strategy}_{pair}"

                logger.info(f"DISPATCHING ORDER: {sig['side']} {abs(order_units)} {pair} [{strategy}] | SL: {sig['stop_px']} | TP: {sig['target_px']}")
                resp = client.create_market_order(
                    instrument=pair,
                    units=order_units,
                    stop_loss=sig["stop_px"],
                    take_profit=sig["target_px"],
                    client_tag=tag
                )

                if "orderFillTransaction" in resp:
                    fill = resp["orderFillTransaction"]
                    logger.info(f"FILLED: {pair} at {fill.get('price')} (Trade ID: {fill.get('id')})")
                    open_trades.append({
                        "instrument": pair,
                        "clientExtensions": {"tag": tag}
                    })
                else:
                    logger.error(f"Execution Failed for {pair}: {json.dumps(resp)}")

            time.sleep(POLL_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e:
            logger.error(f"OANDA API network exception: {e}")
            time.sleep(30)
        except Exception as e:
            logger.error(f"Unexpected execution loop exception: {e}", exc_info=True)
            time.sleep(30)

if __name__ == "__main__":
    run_practice_bot()



def evaluate_breakout_engine(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """
    Donchian Channel Breakout Engine:
      - Channel: 20-period High/Low
      - Macro Filter: 200 SMA trend alignment
      - Sizing: 10% of total available account equity (NAV)
    """
    if len(df) < 210:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    
    curr_idx = len(df) - 1
    c_curr = close.iloc[curr_idx]
    
    # 20-period Donchian channels (excluding current bar)
    don_h = high.iloc[curr_idx - 20:curr_idx].max()
    don_l = low.iloc[curr_idx - 20:curr_idx].min()
    
    # 200 SMA Macro Trend Gate
    sma200 = close.rolling(200).mean()
    sma200_curr = sma200.iloc[curr_idx]
    if pd.isna(sma200_curr):
        return None
        
    tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
    atr14 = tr.rolling(14).mean().iloc[curr_idx]
    if pd.isna(atr14) or atr14 <= 0:
        return None

    pip_val = get_pip_value(symbol)

    # Long Breakout
    if c_curr > don_h and c_curr > sma200_curr:
        stop_px = c_curr - (1.5 * atr14)
        target_px = c_curr + (3.0 * atr14)
        stop_dist = c_curr - stop_px
        if stop_dist > (10 * pip_val):
            return {
                "strategy": "BREAKOUT_DONCHIAN",
                "priority": 2,
                "pair": symbol,
                "side": "BUY",
                "stop_px": stop_px,
                "target_px": target_px,
                "stop_dist": stop_dist
            }

    # Short Breakout
    elif c_curr < don_l and c_curr < sma200_curr:
        stop_px = c_curr + (1.5 * atr14)
        target_px = c_curr - (3.0 * atr14)
        stop_dist = stop_px - c_curr
        if stop_dist > (10 * pip_val):
            return {
                "strategy": "BREAKOUT_DONCHIAN",
                "priority": 2,
                "pair": symbol,
                "side": "SELL",
                "stop_px": stop_px,
                "target_px": target_px,
                "stop_dist": stop_dist
            }

    return None

def calculate_position_units(account_nav: float, risk_pct: float, entry_px: float, stop_px: float, pair: str) -> int:
    """
    Calculates exact trade units based on account equity and risk distance.
    """
    dollar_risk = account_nav * risk_pct
    price_risk_distance = abs(entry_px - stop_px)
    if price_risk_distance <= 0:
        return 0
        
    if pair.startswith("USD"):
        risk_per_unit = price_risk_distance
    elif pair.endswith("JPY"):
        risk_per_unit = price_risk_distance / entry_px
    else:
        risk_per_unit = price_risk_distance
        
    if risk_per_unit <= 0:
        return 0
        
    raw_units = dollar_risk / risk_per_unit
    units = int(round(raw_units, -1))
    return max(units, 100)
