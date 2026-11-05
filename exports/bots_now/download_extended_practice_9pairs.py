#!/usr/bin/env python3

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# ============================================================
# HARD SAFETY LOCKS
# ============================================================

ENV_FILE = Path("/home/moonrider1968/.env")
TARGET_DIR = Path("/home/moonrider1968/bots/data_h4_extended")
BASE_URL = "https://api-fxpractice.oanda.com/v3"

# Frozen original F0 research universe -- DO NOT EXPAND HERE.
PAIRS = [
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CAD",
    "NZD_USD",
    "USD_CHF",
    "EUR_JPY",
    "GBP_JPY",
]

START = "2016-01-01T00:00:00Z"
GRANULARITY = "H4"
COUNT = 5000

# Load existing saved environment.
load_dotenv(ENV_FILE)

API_KEY = (
    os.getenv("OANDA_API_KEY")
    or os.getenv("OANDA_ACCESS_TOKEN")
    or os.getenv("OANDA_API_TOKEN")
)

if not API_KEY:
    print("FATAL: OANDA API token not found in saved environment.")
    sys.exit(1)

# Absolute prohibition against accidental live endpoint.
if "fxpractice.oanda.com" not in BASE_URL:
    print("FATAL: PRACTICE-ONLY SAFETY LOCK FAILED.")
    sys.exit(1)

TARGET_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept-Datetime-Format": "RFC3339",
})

print("=" * 90)
print("PANDA FX — EXTENDED H4 DATA INGESTION")
print("PRACTICE ENDPOINT ONLY")
print("=" * 90)
print(f"Endpoint:    {BASE_URL}")
print(f"Start:       {START}")
print(f"Granularity: {GRANULARITY}")
print(f"Universe:    {len(PAIRS)} frozen canonical pairs")
print(f"Destination: {TARGET_DIR}")
print("Existing canonical /oanda_data/h4 files will NOT be modified.")
print("=" * 90)


def fetch_pair(pair):
    url = f"{BASE_URL}/instruments/{pair}/candles"

    rows = []
    cursor = START
    request_count = 0

    while True:
        params = {
            "granularity": GRANULARITY,
            "price": "M",
            "from": cursor,
            "count": COUNT,
        }

        response = session.get(url, params=params, timeout=30)
        request_count += 1

        if response.status_code != 200:
            raise RuntimeError(
                f"{pair}: HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        candles = response.json().get("candles", [])

        if not candles:
            break

        for c in candles:
            if not c.get("complete", False):
                continue

            mid = c["mid"]

            rows.append({
                "timestamp": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c["volume"]),
            })

        last_time = candles[-1]["time"]

        if len(candles) < COUNT:
            break

        # Protection against a stalled pagination cursor.
        if last_time == cursor:
            raise RuntimeError(f"{pair}: pagination cursor stalled at {cursor}")

        cursor = last_time
        time.sleep(0.15)

    if not rows:
        raise RuntimeError(f"{pair}: zero completed candles returned")

    df = pd.DataFrame(rows)

    # Parse UTC and remove pagination-boundary duplicates.
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    raw_rows = len(df)

    df = (
        df.drop_duplicates(subset=["timestamp"], keep="last")
          .sort_values("timestamp")
          .reset_index(drop=True)
    )

    duplicates_removed = raw_rows - len(df)

    # OHLC integrity.
    bad_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    ).sum()

    # Report intervals larger than normal H4 spacing.
    # Weekend closures will naturally appear here, so these are
    # diagnostic gaps, not automatically data errors.
    delta = df["timestamp"].diff()
    gap_count = int((delta > pd.Timedelta(hours=4)).sum())
    max_gap = delta.max()

    if not df["timestamp"].is_monotonic_increasing:
        raise RuntimeError(f"{pair}: timestamps not monotonic")

    if df["timestamp"].duplicated().any():
        raise RuntimeError(f"{pair}: duplicate timestamps remain")

    if bad_ohlc:
        raise RuntimeError(f"{pair}: {bad_ohlc} invalid OHLC rows")

    outfile = TARGET_DIR / f"{pair}_H4.csv"

    save_df = df.copy()
    save_df["timestamp"] = save_df["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    save_df.to_csv(outfile, index=False)

    return {
        "pair": pair,
        "rows": len(df),
        "first": df["timestamp"].iloc[0],
        "last": df["timestamp"].iloc[-1],
        "duplicates": duplicates_removed,
        "gaps": gap_count,
        "max_gap": max_gap,
        "requests": request_count,
    }


results = []
failed = []

for pair in PAIRS:
    print(f"\nFetching {pair} ...", flush=True)

    try:
        result = fetch_pair(pair)
        results.append(result)

        print(
            f"PASS {pair}: "
            f"{result['rows']:,} bars | "
            f"{result['first']} -> {result['last']} | "
            f"dupes_removed={result['duplicates']} | "
            f">4H_gaps={result['gaps']} | "
            f"max_gap={result['max_gap']} | "
            f"requests={result['requests']}"
        )

    except Exception as exc:
        failed.append((pair, str(exc)))
        print(f"FAIL {pair}: {exc}")


print("\n" + "=" * 90)
print("EXTENDED DATA INGESTION SUMMARY")
print("=" * 90)

for r in results:
    print(
        f"{r['pair']:<8} "
        f"rows={r['rows']:>7,}  "
        f"first={r['first']}  "
        f"last={r['last']}  "
        f"dupes={r['duplicates']:>2}  "
        f"gaps={r['gaps']:>4}"
    )

print("-" * 90)
print(f"PASS: {len(results)}/{len(PAIRS)}")
print(f"FAIL: {len(failed)}/{len(PAIRS)}")

if failed:
    for pair, err in failed:
        print(f"FAILED {pair}: {err}")

    print("\n>>> EXTENDED_DATA_CERTIFICATION = FAIL <<<")
    sys.exit(1)

print("\n>>> EXTENDED_DATA_INGESTION = PASS <<<")
print("NEXT STEP: overlap parity bridge BEFORE viewing pre-2023 strategy performance.")
