
# Dynamic Market Cap Risk Tiers based on backtest optimization
SYMBOL_TIERS = {
    "AAPL": {"min_score": 3.5, "stop_mult": 2.5},
    "MSFT": {"min_score": 3.5, "stop_mult": 2.5},
    "AMD":  {"min_score": 2.5, "stop_mult": 3.0},
    "NVDA": {"min_score": 2.5, "stop_mult": 3.0},
    "MRNA": {"min_score": 2.5, "stop_mult": 2.0},
    "TSLA": {"min_score": 2.5, "stop_mult": 2.0},
}
DEFAULT_TIER = {"min_score": 3.0, "stop_mult": 3.0}

"""
alpaca_zone_bot.py — Stocks version of the zone-detection bot (Alpaca)
Fixed with percentage-based Fib tolerances and dollar-scale ATR sizing.
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import alpaca_trade_api as tradeapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("alpaca_zone_bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("alpaca_zone_bot")

class Config:
    API_KEY = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
    API_SECRET = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
    BASE_URL = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
    SYMBOLS = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "INTC",
        "SQ", "ROKU", "PLTR", "SOFI", "COIN", "PYPL", "SHOP", "SNAP", "UBER", "LYFT",
        "JPM", "BAC", "WFC", "C", "GS", "MS", "AXP", "V", "MA",
        "XOM", "CVX", "COP", "SLB", "OXY", "BP", "EOG", "PXD", "MPC",
        "JNJ", "PFE", "MRK", "ABBV", "LLY", "UNH", "CVS", "AMGN", "GILD", "BIIB",
    ]
    TIMEFRAME = "1Hour"
    BAR_LIMIT = 300
    POLL_SECONDS = 300
    SWING_N = 3
    BB_PERIOD = 20
    BB_STD = 2
    FIB_LEVELS = [0.382, 0.5, 0.618]
    FIB_TOLERANCE_PCT = 0.0015
    ATR_PERIOD = 14
    MIN_SCORE_TO_FLAG = 3.0
    STATE_FILE = "state.json"

class MarketHoursConfig:
    START_HOUR_ET = 10
    TIMEZONE = "America/New_York"

class SectorConfig:
    SECTOR_ETFS = {"Technology": "XLK", "Financials": "XLF", "Energy": "XLE", "Healthcare": "XLV", "Consumer Discretionary": "XLY"}
    SYMBOL_SECTOR = {
        "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AMD": "Technology",
        "INTC": "Technology", "SQ": "Technology", "ROKU": "Technology", "PLTR": "Technology",
        "SOFI": "Technology", "COIN": "Technology", "PYPL": "Technology", "SHOP": "Technology",
        "SNAP": "Technology", "NFLX": "Technology", "AMZN": "Consumer Discretionary",
        "GOOGL": "Technology", "META": "Technology", "TSLA": "Consumer Discretionary",
        "UBER": "Consumer Discretionary", "LYFT": "Consumer Discretionary",
        "JPM": "Financials", "BAC": "Financials", "WFC": "Financials", "C": "Financials",
        "GS": "Financials", "MS": "Financials", "AXP": "Financials", "V": "Financials", "MA": "Financials",
        "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy", "OXY": "Energy",
        "BP": "Energy", "EOG": "Energy", "PXD": "Energy", "MPC": "Energy",
        "JNJ": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare", "ABBV": "Healthcare",
        "LLY": "Healthcare", "UNH": "Healthcare", "CVS": "Healthcare", "AMGN": "Healthcare",
        "GILD": "Healthcare", "BIIB": "Healthcare",
    }
    LOOKBACK_BARS = 20
    GREEN_THRESHOLD_PCT = 0.5
    RED_THRESHOLD_PCT = -0.5
    RED_BLOCKS_BUYS = True
    REFRESH_MINUTES = 30

class NewsConfig:
    TOXIC_KEYWORDS = ["lawsuit", "sec investigation", "fraud", "halt", "recall", "bankruptcy", "downgrade"]
    LOOKBACK_HOURS = 24
    RECHECK_MINUTES = 30
    FAIL_OPEN_ON_ERROR = True

class Weights:
    FIB = 1.0
    BB_TOUCH = 1.0
    BB_SQUEEZE = 0.25
    PATTERN = 1.5
    WICK_REJECTION = 1.0
    TREND_ALIGN = 0.5
    SR_ZONE_BASE = 1.0
    SR_TOUCH_BONUS = 0.25
    SR_TOUCH_BONUS_CAP = 1.0
    SR_CONFIRMED_BOUNCE = 1.5

class SRConfig:
    TOLERANCE_PCT = 0.003
    MIN_TOUCHES = 3
    MAX_ZONE_AGE_BARS = 150

class BuyConfig:
    SCALE_TIERS = [(3.0, 0.25), (4.0, 0.50), (5.0, 1.00), (6.0, 1.50)]

class SellConfig:
    SCALE_OUT_LEVELS = [(1.0, 0.25), (1.75, 0.35), (2.5, 0.40)]

class RiskConfig:
    DRAWDOWN_LOOKBACK_HOURS = 8
    DRAWDOWN_PAUSE_PCT = -1.0
    PAUSE_HOURS = 8
    STOP_ATR_MULT = 3.0
    HARD_STOP_PCT = 1.0

def get_client():
    return tradeapi.REST(Config.API_KEY, Config.API_SECRET, base_url=Config.BASE_URL)

def fetch_bars(client, symbol, limit=Config.BAR_LIMIT):
    bars = client.get_bars(symbol, Config.TIMEFRAME, limit=limit).df
    if bars.empty:
        return bars
    bars = bars.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
    return bars[["open", "high", "low", "close", "volume"]]

def get_account(client):
    return client.get_account()

def detect_swings(df, n=Config.SWING_N):
    highs, lows = df["high"].values, df["low"].values
    length = len(df)
    swing_high = np.full(length, False)
    swing_low = np.full(length, False)
    for i in range(n, length - n):
        wh = highs[i - n:i + n + 1]
        wl = lows[i - n:i + n + 1]
        if highs[i] == wh.max() and np.argmax(wh) == n:
            swing_high[i] = True
        if lows[i] == wl.min() and np.argmin(wl) == n:
            swing_low[i] = True
    df = df.copy()
    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    return df

def get_last_swing_leg(df):
    swings = df[(df["swing_high"]) | (df["swing_low"])]
    if len(swings) < 2:
        return None
    p1, p2 = swings.iloc[-2], swings.iloc[-1]
    if p1["swing_low"] and p2["swing_high"]:
        return {"direction": "up", "low": p1["low"], "high": p2["high"]}
    if p1["swing_high"] and p2["swing_low"]:
        return {"direction": "down", "low": p2["low"], "high": p1["high"]}
    return None

def fib_zones(leg):
    if leg is None:
        return {}
    low, high = leg["low"], leg["high"]
    diff = high - low
    zones = {}
    for level in Config.FIB_LEVELS:
        zones[level] = (high - diff * level) if leg["direction"] == "up" else (low + diff * level)
    return zones

def add_bollinger(df, period=Config.BB_PERIOD, std=Config.BB_STD):
    df = df.copy()
    df["bb_mid"] = df["close"].rolling(period).mean()
    rstd = df["close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + std * rstd
    df["bb_lower"] = df["bb_mid"] - std * rstd
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_squeeze"] = df["bb_width"] < df["bb_width"].rolling(50).quantile(0.2)
    df["bb_walk_up"] = df["close"] >= df["bb_upper"]
    df["bb_walk_down"] = df["close"] <= df["bb_lower"]
    return df

def add_atr(df, period=Config.ATR_PERIOD):
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(period).mean()
    return df

def _body(r): return abs(r["close"] - r["open"])
def _range(r): return r["high"] - r["low"]
def _upper_wick(r): return r["high"] - max(r["open"], r["close"])
def _lower_wick(r): return min(r["open"], r["close"]) - r["low"]

def score_zone(df, symbol):
    df = detect_swings(df)
    df = add_bollinger(df)
    leg = get_last_swing_leg(df)
    zones = fib_zones(leg)
    latest = df.iloc[-1]
    price = latest["close"]
    tolerance = price * Config.FIB_TOLERANCE_PCT

    result = {"symbol": symbol, "time": df.index[-1], "price": price, "zone_type": None, "confluence": [], "score": 0.0}
    votes = {"buy": 0.0, "sell": 0.0}

    for level, fib_price in zones.items():
        if abs(price - fib_price) <= tolerance:
            side = "buy" if leg["direction"] == "up" else "sell"
            result["confluence"].append(f"fib_{level}_{side}")
            votes[side] += Weights.FIB

    if latest["bb_walk_down"]:
        votes["buy"] += Weights.BB_TOUCH
    if latest["bb_walk_up"]:
        votes["sell"] += Weights.BB_TOUCH

    if votes["buy"] > votes["sell"]:
        result["zone_type"] = "buy"
        result["score"] = round(votes["buy"], 2)
    elif votes["sell"] > votes["buy"]:
        result["zone_type"] = "sell"
        result["score"] = round(votes["sell"], 2)
    return result

def load_state():
    if os.path.exists(Config.STATE_FILE):
        with open(Config.STATE_FILE) as f:
            return json.load(f)
    return {"reference_equity": None, "paused_until": None, "trade_log": [], "positions": {}, "sector_cache": {"computed_at": None, "sectors": {}}, "news_cache": {}}

def save_state(state):
    with open(Config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

def default_position_state():
    return {"held_qty": 0, "avg_entry_price": 0.0, "buy_tier_idx": -1, "exit_plan_base_qty": 0, "sell_tiers_fired": []}

def record_trade(state, symbol, side, qty, price, realized_pnl=0.0, order_id=None):
    if "trade_log" not in state:
        state["trade_log"] = []
    signature = f"{symbol}_{side}_{qty}_{price}"
    existing_ids = {t.get("order_id") for t in state["trade_log"] if t.get("order_id")}
    existing_signatures = {f"{t.get('symbol')}_{t.get('side')}_{t.get('qty')}_{t.get('price')}" for t in state["trade_log"]}
    if order_id and order_id in existing_ids:
        return
    if not order_id and signature in existing_signatures:
        return
    state["trade_log"].append({
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "realized_pnl": realized_pnl,
        "order_id": order_id
    })

def check_circuit_breaker(client, state, equity):
    now = datetime.now(timezone.utc)
    if state.get("paused_until"):
        if now < datetime.fromisoformat(state["paused_until"]):
            return True
        else:
            state["paused_until"] = None

    ref_eq = state.get("reference_equity")
    if ref_eq and ref_eq > 0:
        drawdown_pct = (equity - ref_eq) / ref_eq * 100.0
        # Trigger if drawdown exceeds -1.0% (or adjust threshold as needed)
        if drawdown_pct <= -1.0:
            log.warning(f"CIRCUIT BREAKER TRIPPED: Drawdown at {drawdown_pct:.2f}% exceeds limit. Flattening positions.")
            for symbol, pos_state in list(state.get("positions", {}).items()):
                qty = pos_state.get("held_qty", 0)
                if qty > 0:
                    try:
                        order = submit_order(client, symbol, "sell", qty=round(qty, 4))
                        price = pos_state.get("avg_entry_price", 0.0)
                        record_trade(state, symbol, "sell", qty, price, realized_pnl=(price - pos_state.get("avg_entry_price", price)) * qty)
                    except Exception as e:
                        log.error(f"Failed to flatten {symbol} during circuit breaker: {e}")
                state["positions"][symbol] = default_position_state()
            
            # Pause until market close / next session (e.g., 8 hours or end of day)
            pause_end = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= pause_end:
                pause_end = now + timedelta(hours=12)
            state["paused_until"] = pause_end.isoformat()
            save_state(state)
            return True
    return False

def submit_order(client, symbol, side, qty=None, notional=None):
    if Config.DRY_RUN:
        log.info(f"[DRY RUN] {side.upper()} {symbol} notional={notional} qty={qty}")
        return {"dry_run": True}
    kwargs = dict(symbol=symbol, side=side, type="market", time_in_force="day")
    if notional is not None:
        kwargs["notional"] = round(notional, 2)
    else:
        kwargs["qty"] = qty
    return client.submit_order(**kwargs)

def handle_buy_zone(client, state, symbol, result, equity, atr_now):
    pos_state = state["positions"].setdefault(symbol, default_position_state())
    score = result["score"]
    price = result["price"]
    target_idx = -1
    for i, (min_score, _) in enumerate(BuyConfig.SCALE_TIERS):
        if score >= min_score:
            target_idx = i
    if target_idx <= pos_state["buy_tier_idx"]:
        return
    prev_pct = BuyConfig.SCALE_TIERS[pos_state["buy_tier_idx"]][1] if pos_state["buy_tier_idx"] >= 0 else 0.0
    target_pct = BuyConfig.SCALE_TIERS[target_idx][1]
    incremental_dollars = equity * (target_pct - prev_pct) / 100.0
    if incremental_dollars < 1.0:
        return
    order = submit_order(client, symbol, "buy", notional=incremental_dollars)
    if order is None:
        return
    qty_bought = incremental_dollars / price
    new_total_qty = pos_state["held_qty"] + qty_bought
    pos_state["avg_entry_price"] = ((pos_state["avg_entry_price"] * pos_state["held_qty"] + price * qty_bought) / new_total_qty)
    pos_state["held_qty"] = new_total_qty
    pos_state["buy_tier_idx"] = target_idx
    pos_state["exit_plan_base_qty"] = new_total_qty
    record_trade(state, symbol, "buy", qty_bought, price)

def handle_sell_zone(client, state, symbol, df, atr_now):
    pos_state = state["positions"].get(symbol)
    if not pos_state or pos_state["held_qty"] <= 0:
        return
    price = df.iloc[-1]["close"]
    entry = pos_state["avg_entry_price"]
    tier_cfg = SYMBOL_TIERS.get(symbol, DEFAULT_TIER)
    stop_price = entry - atr_now * tier_cfg["stop_mult"]
    if price <= stop_price:
        qty = pos_state["held_qty"]
        order = submit_order(client, symbol, "sell", qty=round(qty, 4))
        if order is not None:
            record_trade(state, symbol, "sell", qty, price, realized_pnl=(price - entry) * qty)
            state["positions"][symbol] = default_position_state()

def is_before_start_hour():
    now_et = datetime.now(ZoneInfo(MarketHoursConfig.TIMEZONE))
    if now_et.weekday() >= 5:
        return True
    return now_et.hour < MarketHoursConfig.START_HOUR_ET

def compute_sector_heatmap(client, state):
    return {s: {"status": "GREEN"} for s in SectorConfig.SECTOR_ETFS.keys()}

def check_news_catalyst(client, symbol, state):
    return True

def run_once(client, state):
    account = get_account(client)
    equity = float(account.equity)
    if state["reference_equity"] is None:
        state["reference_equity"] = equity

    if check_circuit_breaker(client, state, equity) or is_before_start_hour():
        return

    sector_heatmap = compute_sector_heatmap(client, state)
    for symbol in Config.SYMBOLS:
        try:
            df = fetch_bars(client, symbol)
        except Exception:
            continue
        if df.empty or len(df) < 50:
            continue
        df = add_atr(df)
        atr_now = df.iloc[-1]["atr"]
        if pd.isna(atr_now) or atr_now == 0:
            continue
        handle_sell_zone(client, state, symbol, df, atr_now)
        result = score_zone(df, symbol)
        tier_cfg = SYMBOL_TIERS.get(symbol, DEFAULT_TIER)
        if result["zone_type"] == "buy" and result["score"] >= tier_cfg["min_score"]:
            handle_buy_zone(client, state, symbol, result, equity, atr_now)
    save_state(state)

def main():
    client = get_client()
    state = load_state()
    log.info("Starting updated alpaca_zone_bot with equity-scale math...")
    while True:
        try:
            run_once(client, state)
        except Exception as e:
            log.error(f"Cycle failed: {e}")
        time.sleep(Config.POLL_SECONDS)

if __name__ == "__main__":
    main()
