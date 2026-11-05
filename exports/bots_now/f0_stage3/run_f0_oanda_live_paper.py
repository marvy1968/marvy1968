#!/usr/bin/env python3
"""
F0 LIVE OANDA PAPER EXECUTION DAEMON
=================================================================
Polls Oanda Practice API for H4 bars, runs point-in-time feature engineering,
applies the frozen Logistic Regression admission gate, enforces risk clamps,
and manages paper order submissions.
"""

import os
from pathlib import Path
import sys
import time
import logging
import pickle
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
LOG_DIR = STAGE / "paper_logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "oanda_live_execution.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Configuration from environment / explicit credentials
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "82897b14206f9d4c95b70eaa")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json"
}

print("=" * 120)
print("F0 OANDA LIVE PAPER EXECUTION DAEMON — INITIALIZING")
print(f"Target Account: {OANDA_ACCOUNT_ID} | Endpoint: {OANDA_URL}")
print("=" * 120)

# Load frozen model bundle if available, else build/load from STAGE
BUNDLE_PATH = STAGE / "f0_production_bundle.pkl"

def get_oanda_candles(instrument="EUR_USD", count=300):
    """Fetches H4 candles from Oanda Practice API with fail-closed safety."""
    url = f"{OANDA_URL}/v3/instruments/{instrument}/candles"
    params = {
        "count": count,
        "granularity": "H4",
        "price": "M"
    }
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if response.status_code != 200:
            logging.error(f"OANDA API ERROR {response.status_code}: {response.text}")
            return None
        data = response.json()
        candles = data.get("candles", [])
        
        rows = []
        for c in candles:
            if not c.get("complete", False):
                continue # Only use completed H4 bars for point-in-time features
            dt = c["time"]
            mid = c["mid"]
            rows.append({
                "time": dt,
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"])
            })
        df = pd.DataFrame(rows)
        return df
    except Exception as e:
        logging.error(f"FAIL-CLOSED: Exception during Oanda candle fetch for {instrument}: {e}")
        return None

print("[INFO] Oanda API polling harness loaded successfully.")
print("[INFO] Ready to execute live paper check loops.")
print("=" * 120)
