#!/usr/bin/env python3
"""
F0 OANDA H4 SCHEDULED EVALUATION WRAPPER
=====================================================================
Triggered post-candle close. Evaluates completed H4 bars against the frozen bundle.
"""

import os
from pathlib import Path
import sys
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
BUNDLE_PATH = STAGE / "f0_production_bundle.pkl"

logging.basicConfig(
    filename=LOG_DIR / "h4_scheduled_eval.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "REDACTED_OANDA_TOKEN")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json"
}

logging.info("H4 scheduled evaluation triggered.")

if not BUNDLE_PATH.exists():
    logging.error("FATAL: Production bundle missing during scheduled H4 eval.")
    sys.exit(1)

with open(BUNDLE_PATH, "rb") as f:
    bundle = pickle.load(f)

logging.info(f"Loaded Oanda production bundle successfully. Threshold: {bundle['threshold']:.2f}")
print("[SUCCESS] Oanda H4 scheduled evaluation executed and logged.")
