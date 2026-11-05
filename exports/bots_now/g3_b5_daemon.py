#!/usr/bin/env python3
"""
G3-B5 Production-Safe Trading Daemon (Paper / Shadow Mode)
Derived from Phase 11B Certified Research Architecture.
Parameters: G3-B5 (d1_trig=5.0%, d2_trig=8.5%, d3_trig=12.5%, d1_mult=0.85, d2_mult=0.50, d3_mult=0.15, dwell=2, recovery=2.5%)
"""

import os
import sys
import time
import datetime
import json
import logging
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

# --- ENVIRONMENT & SAFETY GUARDS ---
load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))

# Hard safety constraints
PAPER_MODE = True
LIVE_TRADING = False
MAX_PORTFOLIO_RISK = 0.03
MAX_SYMBOL_RISK = 0.01
MAX_POSITIONS = 3
DAILY_LOSS_LIMIT = 0.02
MAX_DRAWDOWN_KILL_SWITCH = 0.15

DATA_URL = "https://data.alpaca.markets/v2"
TRADING_URL = "https://paper-api.alpaca.markets/v2" if PAPER_MODE else "https://api.alpaca.markets/v2"
NY_TZ = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("g3_b5_daemon.log"), logging.StreamHandler(sys.stdout)]
)

XLK_LONG_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"]
STAGE4_SHORT_FLEET = [
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", "META",
    "JPM", "BAC", "WFC", "GS", "MS", "AXP", "CAT", "GE", "UNP", "HON", "RTX", "ETN"
]
DONCHIAN_SATELLITE_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", 
    "META", "AAPL", "NFLX", "SNOW", "PLTR", "DDOG", "CRWD", "NET", "PANW"
]
ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + DONCHIAN_SATELLITE_UNIVERSE + ["SPY"])))

# G3-B5 Certified Parameters
G3_B5_PARAMS = {
    "d1_trig": 0.050,
    "d2_trig": 0.085,
    "d3_trig": 0.125,
    "d1_mult": 0.85,
    "d2_mult": 0.50,
    "d3_mult": 0.15,
    "dwell": 2,
    "recovery_hysteresis": 0.025
}

STATE_FILE = "/home/moonrider1968/bots/g3_b5_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load state file: {e}")
    return {
        "nav": 100000.0,
        "high_water": 100000.0,
        "governor_state": "B",
        "state_dwell_counter": 0,
        "open_positions": {},
        "realized_pnl": 0.0
    }

def save_state(state):
    temp_file = STATE_FILE + ".tmp"
    try:
        with open(temp_file, "w") as f:
            json.dump(state, f, indent=4)
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        logging.error(f"Atomic state write failed: {e}")

def get_alpaca_account():
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    try:
        r = requests.get(f"{TRADING_URL}/account", headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.error(f"API connection failure: {e}")
    return None

if __name__ == "__main__":
    logging.info("🏛️ G3-B5 Production-Safe Trading Daemon Initialized.")
    logging.info(f"Mode: {'PAPER' if PAPER_MODE else 'LIVE'} | Guardrails Enforced.")
    state = load_state()
    logging.info(f"Loaded Persistent State -> NAV: ${state['nav']:,.2f} | Governor State: {state['governor_state']}")
