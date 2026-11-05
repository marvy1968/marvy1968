import os, time, datetime, json
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

pd.set_option('future.no_silent_downcasting', True)

load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
DATA_URL = "https://data.alpaca.markets/v2"
NY_TZ = ZoneInfo("America/New_York")

# We import / inspect the exact canonical integration engine
CANONICAL_PATH = "/home/moonrider1968/bots/run_config_b_d3_integration.py"
print(f"Reading canonical engine from {CANONICAL_PATH}...")

with open(CANONICAL_PATH, "r") as f:
    canonical_code = f.read()

# Verify that the canonical engine contains the exact validated functions
assert "def run_adaptive_simulation" in canonical_code, "Canonical simulation function not found."
print("✅ Canonical engine successfully loaded.")
