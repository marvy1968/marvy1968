#!/usr/bin/env python3
"""
PHASE 3C — SECTOR TOP-DOWN PRODUCTION SHADOW BRIDGE
Hardened State Machine, Alpaca Calendar Sync, & Open-Execution Guards
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Deterministic environment loading
load_dotenv("/home/moonrider1968/.env")

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("CRITICAL: ALPACA_API_KEY or ALPACA_SECRET_KEY missing from /home/moonrider1968/.env")
    sys.exit(1)

SHADOW_MODE = True
NY_TZ = ZoneInfo("America/New_York")

DATA_DIR = Path("/home/moonrider1968/bots/shadow_runtime")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "shadow_state.json"
JOURNAL_FILE = DATA_DIR / "shadow_audit_journal.log"

UNIVERSE = ["AAPL", "NVDA", "MSFT", "AMD", "TSLA", "AMZN", "GOOGL", "META", "XLK", "QQQ"]
BENCHMARK = "SPY"

RISK_PER_TRADE_PCT = 0.0035          # 0.35% NAV Risk
MAX_CONCURRENT_POSITIONS = 3         # Max 3 open slots
MAX_SINGLE_POS_NOTIONAL_PCT = 0.333  # 33.3% Single-position gross cap
MAX_PORTFOLIO_GROSS_PCT = 1.000      # 100.0% Portfolio gross cap
SLIPPAGE_PCT = 0.0005                # 0.05% Open gap slippage

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


def get_ny_now() -> datetime:
    return datetime.now(NY_TZ)


def log_journal(message: str):
    timestamp = get_ny_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(JOURNAL_FILE, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "schema_version": 2,
            "active_positions": {},
            "pending_entries": {},
            "pending_exits": {},
            "last_scan_date": None,
            "last_fill_date": None,
        }
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_active_trading_day(target_date) -> bool:
    """Queries Alpaca calendar to check if target_date is an official NYSE trading session."""
    try:
        cal = trading_client.get_calendar(GetCalendarRequest(start=target_date, end=target_date))
        return len(cal) > 0
    except Exception as e:
        log_journal(f"WARNING: Calendar check failed ({e}). Defaulting to weekday check.")
        return target_date.weekday() < 5


def get_previous_trading_day(target_date) -> str:
    """Finds the most recent NYSE trading day prior to target_date."""
    lookback_start = target_date - timedelta(days=10)
    try:
        cal = trading_client.get_calendar(GetCalendarRequest(start=lookback_start, end=target_date - timedelta(days=1)))
        if cal:
            return str(cal[-1].date)
    except Exception as e:
        log_journal(f"WARNING: Prior trading day lookup failed ({e}).")
    # Fallback to simple weekday offset
    offset = 3 if target_date.weekday() == 0 else 1
    return str(target_date - timedelta(days=offset))


def fetch_historical_bars() -> Dict[str, pd.DataFrame]:
    ny_now = get_ny_now()
    end_dt = ny_now - timedelta(minutes=16)
    start_dt = end_dt - timedelta(days=365)

    symbols = UNIVERSE + [BENCHMARK]
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start_dt,
        end=end_dt,
        feed=DataFeed.IEX,
    )
    bars = data_client.get_stock_bars(request)
    dfs = {}

    for sym in symbols:
        try:
            df = bars.df.xs(sym).sort_index().copy()
            close = df["close"]
            df["ema20"] = close.ewm(span=20, adjust=False).mean()
            df["sma50"] = close.rolling(50, min_periods=50).mean()
            prev_close = close.shift(1)
            tr = np.maximum(
                df["high"] - df["low"],
                np.maximum((df["high"] - prev_close).abs(), (df["low"] - prev_close).abs())
            )
            df["atr14"] = tr.rolling(14, min_periods=14).mean()
            dfs[sym] = df
        except KeyError:
            log_journal(f"WARNING: Symbol {sym} missing from feed.")
    return dfs


# =============================================================================
# PHASE 1: POST-MARKET SCAN (16:15 ET)
# =============================================================================

def run_post_market_scan():
    today_date = get_ny_now().date()
    today_str = str(today_date)
    log_journal(f"=== RUNNING POST-MARKET SCAN (Trading Date: {today_str}) ===")

    if not is_active_trading_day(today_date):
        log_journal(f"ABORT: {today_str} is not an active trading day.")
        return

    state = load_state()
    dfs = fetch_historical_bars()

    spy_df = dfs.get(BENCHMARK)
    if spy_df is None or len(spy_df) < 21:
        log_journal("ERROR: Insufficient SPY data.")
        return

    spy_now = float(spy_df["close"].iloc[-1])
    spy_base = float(spy_df["close"].iloc[-21])
    spy_perf = (spy_now / spy_base) - 1.0

    state["pending_exits"] = {}
    state["pending_entries"] = {}

    # 1. Identify EMA20 Break Exits
    for sym, pos in list(state["active_positions"].items()):
        if sym not in dfs:
            continue
        row = dfs[sym].iloc[-1]
        if row["close"] < row["ema20"]:
            state["pending_exits"][sym] = {
                "reason": "EMA20_BREAKDOWN",
                "signal_close": float(row["close"]),
                "ema20": float(row["ema20"]),
            }
            log_journal(f"[PENDING EXIT] {sym} | Close: ${row['close']:.2f} < EMA20: ${row['ema20']:.2f}")

    # 2. Identify Relative Strength Entries
    occupied_syms = set(state["active_positions"].keys()) - set(state["pending_exits"].keys())
    available_slots = MAX_CONCURRENT_POSITIONS - len(occupied_syms)

    for sym in UNIVERSE:
        if sym in occupied_syms or sym not in dfs or len(dfs[sym]) < 50:
            continue

        df = dfs[sym]
        row = df.iloc[-1]
        stock_base = float(df["close"].iloc[-21])
        stock_perf = (float(row["close"]) / stock_base) - 1.0

        if stock_perf > (spy_perf + 0.03) and row["close"] > row["ema20"] > row["sma50"]:
            initial_stop = float(row["ema20"]) - float(row["atr14"])
            state["pending_entries"][sym] = {
                "signal_close": float(row["close"]),
                "initial_stop": initial_stop,
                "atr14": float(row["atr14"]),
                "ema20": float(row["ema20"]),
                "stock_perf_20d": stock_perf,
                "spy_perf_20d": spy_perf,
            }
            log_journal(f"[PENDING ENTRY] {sym} | Close: ${row['close']:.2f} | Initial Stop: ${initial_stop:.2f}")

    state["last_scan_date"] = today_str
    save_state(state)
    log_journal("=== POST-MARKET SCAN COMPLETE ===")


# =============================================================================
# PHASE 2: MARKET OPEN EXECUTION (09:30:30 ET)
# =============================================================================

def run_market_open_execution(ignore_time_window: bool = False):
    ny_now = get_ny_now()
    today_date = ny_now.date()
    today_str = str(today_date)
    log_journal(f"=== RUNNING MARKET OPEN EXECUTION (Trading Date: {today_str}) ===")

    # Guard 1: Market Trading Day Check
    if not is_active_trading_day(today_date):
        log_journal(f"ABORT: {today_str} is not an active NYSE trading session.")
        return

    # Guard 2: Strict Open Execution Time Window (09:30:00 to 09:35:00 ET)
    current_time_minutes = ny_now.hour * 60 + ny_now.minute
    market_open_minutes = 9 * 60 + 30
    if not ignore_time_window:
        if not (market_open_minutes <= current_time_minutes <= market_open_minutes + 5):
            log_journal(f"ABORT: Current time {ny_now.strftime('%H:%M:%S %Z')} is outside the 09:30–09:35 execution window.")
            return

    state = load_state()

    # Guard 3: Idempotency Protection
    if state.get("last_fill_date") == today_str:
        log_journal(f"ABORT: Fills already executed for {today_str}.")
        return

    # Guard 4: Previous Trading Day Signal Sequence Validation
    expected_scan_date = get_previous_trading_day(today_date)
    if state.get("last_scan_date") != expected_scan_date:
        log_journal(f"ABORT: Signal state mismatch. Expected scan date: {expected_scan_date}, found: {state.get('last_scan_date')}.")
        return

    if not state.get("pending_entries") and not state.get("pending_exits"):
        log_journal("No pending entries or exits staged. Setting last_fill_date and exiting.")
        state["last_fill_date"] = today_str
        save_state(state)
        return

    account = trading_client.get_account()
    equity = float(account.equity)

    # 1. Execute Pending Exits
    for sym, exit_info in list(state["pending_exits"].items()):
        if sym in state["active_positions"]:
            pos = state["active_positions"][sym]
            client_order_id = f"EXIT_{sym}_{today_str.replace('-', '')}"
            log_journal(f"[SHADOW FILL: EXIT] {sym} x {pos['qty']} shares | Reason: {exit_info['reason']} | ClientOrderID: {client_order_id}")
            del state["active_positions"][sym]
    state["pending_exits"] = {}

    # 2. Execute Pending Entries with Post-Open Gap Recalculation
    available_slots = MAX_CONCURRENT_POSITIONS - len(state["active_positions"])

    for sym, entry_info in list(state["pending_entries"].items()):
        if available_slots <= 0:
            log_journal(f"[SLOT CAPPED] {sym} skipped — max portfolio capacity reached.")
            continue

        try:
            req = StockLatestBarRequest(symbol_or_symbols=sym, feed=DataFeed.IEX)
            bar = data_client.get_stock_latest_bar(req)[sym]
            open_px = float(bar.open)
        except Exception as e:
            log_journal(f"ERROR: Failed to fetch live open quote for {sym} ({e}). Skipping fill.")
            continue

        entry_fill = open_px * (1.0 + SLIPPAGE_PCT)
        initial_stop = entry_info["initial_stop"]

        # Invalidate if opening price gaps below stop
        if initial_stop >= entry_fill:
            log_journal(f"[GAP INVALIDATED] {sym} Open (${open_px:.2f}) opened at/below initial stop (${initial_stop:.2f}).")
            continue

        risk_per_share = entry_fill - initial_stop
        risk_dollars = equity * RISK_PER_TRADE_PCT
        qty_risk = int(risk_dollars / risk_per_share)
        qty_single_cap = int((equity * MAX_SINGLE_POS_NOTIONAL_PCT) / entry_fill)

        curr_gross = sum(p["entry_price"] * p["qty"] for p in state["active_positions"].values())
        qty_gross_cap = int(max(0.0, (equity * MAX_PORTFOLIO_GROSS_PCT) - curr_gross) / entry_fill)

        qty = min(qty_risk, qty_single_cap, qty_gross_cap)
        if qty <= 0:
            log_journal(f"[SIZING ZERO] {sym} allocation cap resulted in 0 shares.")
            continue

        client_order_id = f"ENTRY_{sym}_{today_str.replace('-', '')}"
        stop_order_id = f"STOP_{sym}_{today_str.replace('-', '')}"

        log_journal(
            f"[SHADOW FILL: BUY] {sym} x {qty} @ Fill: ${entry_fill:.2f} (Open: ${open_px:.2f}) | "
            f"Stop: ${initial_stop:.2f} (Risk: ${risk_per_share*qty:,.2f}) | ID: {client_order_id}"
        )

        state["active_positions"][sym] = {
            "entry_date": today_str,
            "entry_price": entry_fill,
            "initial_stop": initial_stop,
            "current_stop": initial_stop,
            "qty": qty,
            "risk_per_share": risk_per_share,
        }
        available_slots -= 1

    state["pending_entries"] = {}
    state["last_fill_date"] = today_str
    save_state(state)
    log_journal("=== MARKET OPEN EXECUTION COMPLETE ===")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--open":
        ignore_window = "--force" in sys.argv
        run_market_open_execution(ignore_time_window=ignore_window)
    else:
        run_post_market_scan()
