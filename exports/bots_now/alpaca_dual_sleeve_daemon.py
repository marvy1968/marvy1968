#!/usr/bin/env python3
"""
Institutional Alpaca Dual-Sleeve Trading Daemon (Production Certified)
Features:
- Sleeve A: Oliver Velez 20/200 EMA Reversal (Fixed 3.0R Target Bracket)
- Sleeve B: G3-B5 30D Donchian Trend + Dynamic 2.0x ATR Ratchet Trailing Stop
- Macro Regime Gate (SPY > EMA20 > SMA50 with EMA20 slope > 0.002)
- ATR Volatility Filter (ATR_ratio <= 0.055, ATR_expansion <= 1.35)
- Dynamic Active Stop-Loss Trailing via Alpaca API order replacement
- Atomic Local Sleeve State Tracking (active_sleeve_positions.json)
"""

import os
import sys
import time
import json
import logging
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path("/home/moonrider1968/bots/stock_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "alpaca_dual_sleeve_daemon.log"
STATE_FILE = DATA_DIR / "active_sleeve_positions.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)

API_KEY = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

UNIVERSE = ["AAPL", "NVDA", "MSFT", "AMD", "TSLA", "AMZN", "GOOGL", "META", "XLK", "QQQ"]
BENCHMARK = "SPY"
ALL_SYMBOLS = UNIVERSE + [BENCHMARK]
RISK_PER_TRADE_PCT = 0.0035
MAX_TOTAL_POSITIONS = 3
MAX_VOL_RATIO = 0.055
MAX_ATR_EXPANSION = 1.35

def load_journal():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_journal(state):
    tmp_path = STATE_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2))
    tmp_path.replace(STATE_FILE)

def get_account_nav():
    res = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    res.raise_for_status()
    return float(res.json()["equity"])

def fetch_daily_bars(symbols, limit=1000):
    dfs = {}
    requested = list(dict.fromkeys(list(symbols) + [BENCHMARK]))

    for sym in requested:
        bars = []
        try:
            params = {
                "symbols": sym,
                "timeframe": "1Day",
                "start": "2023-01-01",
                "limit": limit,
                "adjustment": "split",
                "feed": "sip",
            }
            res = requests.get(f"{DATA_URL}/stocks/bars", headers=HEADERS, params=params, timeout=15)
            if res.status_code == 200:
                bars = res.json().get("bars", {}).get(sym, [])
        except Exception as e:
            logging.warning(f"API fetch exception for {sym}: {e}")

        df = None
        if len(bars) >= 60:
            df = pd.DataFrame(bars)
            df["timestamp"] = pd.to_datetime(df["t"], utc=True)
            df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "vwap"})
            df = df[["timestamp", "open", "high", "low", "close", "volume", "vwap"]].set_index("timestamp").sort_index()
            df.to_csv(DATA_DIR / f"{sym}_daily.csv")
        else:
            cache_file = DATA_DIR / f"{sym}_daily.csv"
            if cache_file.exists():
                df = pd.read_csv(cache_file, parse_dates=["timestamp"]).set_index("timestamp").sort_index()

        if df is None or len(df) < 60:
            continue

        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["sma200"] = df["close"].rolling(min(200, len(df))).mean()
        df["donch_high_30"] = df["high"].rolling(30).max().shift(1)

        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1)))
        )
        df["atr14"] = tr.rolling(14).mean()
        df["atr_ratio"] = df["atr14"] / df["close"]
        df["atr_expansion"] = df["atr14"] / df["atr14"].rolling(30).mean()

        clean_df = df.dropna()
        if not clean_df.empty:
            dfs[sym] = clean_df

    return dfs

def submit_bracket_order(symbol, qty, side, take_profit_px=None, stop_loss_px=None):
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day"
    }
    if take_profit_px and stop_loss_px:
        payload["order_class"] = "bracket"
        payload["take_profit"] = {"limit_price": str(round(take_profit_px, 2))}
        payload["stop_loss"] = {"stop_price": str(round(stop_loss_px, 2))}
    elif stop_loss_px:
        payload["order_class"] = "oto"
        payload["stop_loss"] = {"stop_price": str(round(stop_loss_px, 2))}

    res = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=10)
    res.raise_for_status()
    return res.json()

def update_open_trend_stops(open_positions, journal, dfs):
    res = requests.get(f"{BASE_URL}/v2/orders?status=open&nested=true", headers=HEADERS, timeout=10)
    open_orders = res.json() if res.status_code == 200 else []

    for sym, pos_data in open_positions.items():
        if sym not in journal or journal[sym].get("sleeve") != "TREND":
            continue
        
        df = dfs.get(sym)
        if df is None:
            continue
        row = df.iloc[-1]
        
        current_stop = journal[sym].get("current_stop", 0.0)
        new_ratchet_stop = round(float(row["close"]) - (2.0 * float(row["atr14"])), 2)

        if new_ratchet_stop > current_stop:
            logging.info(f"🔄 Ratchet trailing stop for {sym} (TREND): ${current_stop:.2f} -> ${new_ratchet_stop:.2f}")
            stop_order = next((o for o in open_orders if o["symbol"] == sym and o["type"] in ["stop", "stop_limit"]), None)
            if stop_order:
                order_id = stop_order["id"]
                patch_payload = {"stop_price": str(new_ratchet_stop)}
                patch_res = requests.patch(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, json=patch_payload, timeout=10)
                if patch_res.status_code == 200:
                    logging.info(f"  ✓ Successfully updated stop order on Alpaca for {sym} to ${new_ratchet_stop:.2f}")
                    journal[sym]["current_stop"] = new_ratchet_stop
                else:
                    logging.warning(f"  ✗ Stop patch failed ({patch_res.status_code}): {patch_res.text}")
            else:
                journal[sym]["current_stop"] = new_ratchet_stop

def run_portfolio_cycle():
    if not API_KEY or not API_SECRET:
        logging.error("Alpaca credentials missing from environment. Aborting.")
        return

    nav = get_account_nav()
    journal = load_journal()
    logging.info(f"=== Starting Alpaca Dual-Sleeve Cycle | NAV: ${nav:,.2f} ===")
    
    dfs = fetch_daily_bars(UNIVERSE)
    spy_df = dfs.get(BENCHMARK)
    if spy_df is None or len(spy_df) < 60:
        logging.error("SPY data unavailable. Aborting cycle.")
        return

    spy_bar = spy_df.iloc[-1]
    spy_ema_slope = (spy_df["ema20"].iloc[-1] / spy_df["ema20"].iloc[-6]) - 1.0
    macro_bull = bool(spy_bar["close"] > spy_bar["ema20"] > spy_bar["sma50"] and spy_ema_slope > 0.002)
    logging.info(f"Macro SPY: {'BULLISH' if macro_bull else 'DEFENSIVE'} (Slope: {spy_ema_slope:+.4f}) | NAV: ${nav:,.2f}")

    res = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
    open_positions = {p["symbol"]: p for p in res.json()}
    
    active_syms = set(open_positions.keys())
    for s in list(journal.keys()):
        if s not in active_syms:
            logging.info(f"Position closed on broker: {s} (Removed from active journal)")
            del journal[s]

    update_open_trend_stops(open_positions, journal, dfs)
    save_journal(journal)

    logging.info(f"Active Portfolio Positions: {len(open_positions)}/{MAX_TOTAL_POSITIONS}")

    if not macro_bull or len(open_positions) >= MAX_TOTAL_POSITIONS:
        logging.info("Macro defensive or position cap reached. No new entries.")
        return

    for sym in UNIVERSE:
        if sym in open_positions or len(open_positions) >= MAX_TOTAL_POSITIONS:
            continue

        df = dfs.get(sym)
        if df is None or len(df) < 60:
            continue
        row = df.iloc[-1]

        if row["atr_ratio"] > MAX_VOL_RATIO or row["atr_expansion"] > MAX_ATR_EXPANSION:
            continue

        velez_count = sum(1 for p in journal.values() if p.get("sleeve") == "VELEZ")
        if (
            velez_count < 1
            and row["close"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.015
            and row["close"] > row["open"]
        ):
            entry_px = float(row["close"])
            stop_px = float(row["low"]) - (0.5 * float(row["atr14"]))
            init_risk = entry_px - stop_px
            if init_risk > 0:
                qty = int((nav * RISK_PER_TRADE_PCT) / init_risk)
                if qty > 0:
                    tp_px = entry_px + (3.0 * init_risk)
                    order = submit_bracket_order(sym, qty, "buy", take_profit_px=tp_px, stop_loss_px=stop_px)
                    logging.info(f"🚀 [VELEZ BRACKET] {sym} | Qty: {qty} | Entry: ~${entry_px:.2f} | Stop: ${stop_px:.2f} | TP: ${tp_px:.2f}")
                    journal[sym] = {"sleeve": "VELEZ", "entry_px": entry_px, "current_stop": stop_px, "qty": qty}
                    open_positions[sym] = order
                    save_journal(journal)
                    continue

        trend_count = sum(1 for p in journal.values() if p.get("sleeve") == "TREND")
        if trend_count < 2 and row["close"] > row["donch_high_30"]:
            entry_px = float(row["close"])
            stop_px = entry_px - (2.0 * float(row["atr14"]))
            init_risk = entry_px - stop_px
            if init_risk > 0:
                qty = int((nav * RISK_PER_TRADE_PCT) / init_risk)
                if qty > 0:
                    order = submit_bracket_order(sym, qty, "buy", stop_loss_px=stop_px)
                    logging.info(f"📈 [G3-B5 DONCHIAN] {sym} | Qty: {qty} | Entry: ~${entry_px:.2f} | Stop: ${stop_px:.2f}")
                    journal[sym] = {"sleeve": "TREND", "entry_px": entry_px, "current_stop": stop_px, "qty": qty}
                    open_positions[sym] = order
                    save_journal(journal)

if __name__ == "__main__":
    run_portfolio_cycle()
