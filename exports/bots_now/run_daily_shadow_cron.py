#!/usr/bin/env python3
"""
Daily Shadow Runner for Kraken G3-B5.
Enforces UTC boundary ingestion (only fully closed prior daily bars),
ensures idempotent single execution per calendar session, and persists portfolio state.
"""
import os
import sys
import json
import logging
import datetime
import pandas as pd
import numpy as np

import run_stitched_oos_engine as canon
from kraken_g3b5_shadow_v3 import KrakenG3B5ShadowDaemon

STATE_FILE = "/home/moonrider1968/bots/shadow_state.json"
MANIFEST_PATH = "/home/moonrider1968/bots/crypto_data/universe_manifest.csv"
TELEMETRY_LOG = "/home/moonrider1968/bots/shadow_telemetry.log"
INITIAL_CAPITAL = 100_000.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(TELEMETRY_LOG, mode="a")
    ]
)

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load state: {e}")
    return {
        "nav": INITIAL_CAPITAL,
        "last_processed_date": None,
        "execution_history": []
    }

def save_state(state: dict):
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_FILE)

def run_daily_shadow_cycle():
    logging.info("=" * 80)
    logging.info("STARTING G3-B5 DAILY SHADOW CRON CYCLE")
    logging.info("=" * 80)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    target_bar_date = (now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    logging.info(f"Execution Target (Prior Closed UTC Bar): {target_bar_date}")

    state = load_state()
    if state.get("last_processed_date") == target_bar_date:
        logging.info(f"Target bar {target_bar_date} has already been processed. Idempotent exit (0).")
        return 0

    manifest, dfs = canon.load_data()
    btc_df = dfs.get("BTC/USD")
    if btc_df is None:
        logging.error("FATAL: BTC/USD data unavailable. Failing closed.")
        return 1

    target_ts = pd.Timestamp(target_bar_date, tz="UTC")
    if target_ts not in btc_df.index:
        logging.error(f"FATAL: Target bar {target_bar_date} not present in BTC data. Failing closed.")
        return 1

    # Slice all DataFrames up to and including the target closed bar
    sliced_dfs = {}
    for sym, df in dfs.items():
        if target_ts in df.index:
            sliced = df.loc[:target_ts]
            if len(sliced) >= 60:
                sliced_dfs[sym] = sliced

    if "BTC/USD" not in sliced_dfs:
        logging.error("FATAL: Insufficient lookback for BTC/USD on target date. Failing closed.")
        return 1

    daemon = KrakenG3B5ShadowDaemon(manifest_path=MANIFEST_PATH)
    nav_input = float(state.get("nav", INITIAL_CAPITAL))

    result = daemon.run_daily_shadow_cycle(sliced_dfs, simulated_nav=nav_input, target_date_str=target_bar_date)

    if result.get("status") != "SUCCESS":
        logging.error(f"FATAL: Shadow daemon returned non-success: {result}")
        return 1

    state["nav"] = result["nav_at_close"]
    state["last_processed_date"] = target_bar_date
    state["execution_history"].append({
        "date": target_bar_date,
        "nav": result["nav_at_close"],
        "open_positions": result["open_positions"],
        "timestamp": now_utc.isoformat()
    })
    save_state(state)

    logging.info(f"SHADOW CYCLE SUCCESS: Date: {target_bar_date} | NAV: ${result['nav_at_close']:,.2f} | Positions: {result['open_positions']}")
    return 0

if __name__ == "__main__":
    sys.exit(run_daily_shadow_cycle())
