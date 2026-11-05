import os
import json
import logging
from pathlib import Path
import pandas as pd
import numpy as np
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ENV_PATH = Path("/home/moonrider1968/bots/.env")
DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
JOURNAL_PATH = Path("/home/moonrider1968/bots/trade_journal.json")

TECH_SYMBOLS = {"AAPL", "AMZN", "AVGO", "GOOGL", "META", "MSFT", "NFLX", "NVDA"}

SECTORS = {
    "AAPL": "TECH_HW", "AMZN": "TECH_CONS", "AVGO": "TECH_SEMI", "CAT": "IND_MACH",
    "COP": "ENERGY_E&P", "COST": "CONS_STAPLE", "CVX": "ENERGY_INTG", "GE": "IND_AERO",
    "GOOGL": "TECH_INTERNET", "GS": "FIN_BANK", "JPM": "FIN_BANK", "META": "TECH_INTERNET",
    "MSFT": "TECH_SW", "NFLX": "TECH_MEDIA", "NVDA": "TECH_SEMI", "PG": "CONS_STAPLE",
    "QQQ": "INDEX_TECH", "SPY": "INDEX_BROAD", "WMT": "CONS_STAPLE", "XOM": "ENERGY_INTG"
}

SYMBOLS = list(SECTORS.keys())

# Load Alpaca API Keys
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

API_KEY = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type": "application/json"
}

def get_account_state():
    res = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    res.raise_for_status()
    acc = res.json()
    nav = float(acc.get("portfolio_value", 0))
    cash = float(acc.get("cash", 0))
    return nav, cash

def get_broker_positions():
    res = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
    res.raise_for_status()
    return {p["symbol"]: p for p in res.json()}

def load_journal():
    if JOURNAL_PATH.exists():
        try:
            return json.loads(JOURNAL_PATH.read_text())
        except Exception:
            return {}
    return {}

def save_journal(journal):
    JOURNAL_PATH.write_text(json.dumps(journal, indent=2))

def evaluate_signals_and_stage():
    nav, cash = get_account_state()
    positions = get_broker_positions()
    journal = load_journal()

    logging.info("=" * 80)
    logging.info(f"PORTFOLIO SCAN | NAV: ${nav:,.2f} | Cash: ${cash:,.2f} | Active Positions: {len(positions)}")
    logging.info("=" * 80)

    # 1. Firewall Capacity Checks
    active_tech_count = sum(1 for sym in positions if sym in TECH_SYMBOLS)
    active_sectors = {SECTORS.get(sym, "OTHER") for sym in positions}

    if len(positions) >= 3:
        logging.info("⛔ Slot capacity saturated (3/3). No new breakout admissions permitted.")
        return

    # 2. Daily Base Risk Sizing (0.50% NAV)
    base_dollar_risk = nav * 0.0050

    signals = []
    for sym in SYMBOLS:
        if sym in positions:
            continue

        p = DATA_DIR / f"{sym}.csv"
        if not p.exists():
            continue

        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        if len(df) < 60:
            continue

        df["high_55"] = df["high"].shift(1).rolling(55).max()
        df["low_20"] = df["low"].shift(1).rolling(20).min()

        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()

        last_bar = df.iloc[-1]
        prev_bar = df.iloc[-2]

        close_px = float(last_bar["close"])
        high_55 = float(last_bar["high_55"])
        prev_close = float(prev_bar["close"])
        prev_h55 = float(prev_bar["high_55"])
        atr = float(last_bar["atr14"])

        # Certified Breakout Check (Close > 55D High from prior day)
        if close_px > high_55 and prev_close <= prev_h55:
            r_dist = 1.5 * atr
            stop_px = close_px - r_dist
            qty = int(base_dollar_risk / r_dist)

            signals.append({
                "symbol": sym,
                "close": close_px,
                "high_55": high_55,
                "atr14": atr,
                "r_dist": r_dist,
                "stop_px": stop_px,
                "qty": qty,
                "sector": SECTORS.get(sym, "OTHER"),
                "is_tech": sym in TECH_SYMBOLS
            })

    logging.info(f"Breakout Candidates Identified: {len(signals)}")
    for sig in signals:
        sym = sig["symbol"]
        is_tech = sig["is_tech"]
        sec = sig["sector"]

        # Firewall Filter Checks
        if len(positions) >= 3:
            logging.warning(f"Skipping {sym}: Port capacity limit reached.")
            continue
        if is_tech and active_tech_count >= 2:
            logging.warning(f"Skipping {sym}: Tech sector exposure cap (2/2) reached.")
            continue
        if sec in active_sectors and sec not in ["INDEX_TECH", "INDEX_BROAD"]:
            logging.warning(f"Skipping {sym}: Subsector {sec} already active.")
            continue

        logging.info(f"✅ STAGING BREAKOUT: {sym} | Est Qty: {sig['qty']} | Ref Stop: ${sig['stop_px']:.2f} | Risk: ${base_dollar_risk:.2f}")

if __name__ == "__main__":
    evaluate_signals_and_stage()
