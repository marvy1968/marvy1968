#!/usr/bin/env python3
"""
INGEST CANONICAL 1-MIN SIP DATA FOR HIGH-BETA SMALL/MID-CAP UNIVERSE
"""

import os
import sys
from pathlib import Path
import datetime as dt
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("[ERROR] ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in your environment.")
    sys.exit(1)

OUT_DIR = Path("/home/moonrider1968/bots/data_midcap_sip_1m")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MIDCAP_UNIVERSE = [
    "SMCI", "CELH", "DUOL", "AFRM", "UPST",
    "MARA", "RIOT", "SYM", "APP", "IOT"
]

START = dt.datetime(2021, 1, 4, 0, 0, tzinfo=dt.timezone.utc)
END = dt.datetime(2024, 12, 31, 23, 59, tzinfo=dt.timezone.utc)

client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

print("=" * 80)
print("FETCHING HIGH-BETA SMALL/MID-CAP 1-MIN SIP DATA")
print(f"Directory: {OUT_DIR}")
print(f"Timeframe: {START.date()} to {END.date()}")
print("=" * 80)

for sym in MIDCAP_UNIVERSE:
    out_file = OUT_DIR / f"{sym}_1MIN_SIP.parquet"
    if out_file.exists():
        print(f"  [EXISTS] {sym:<6} already cached. Skipping.")
        continue

    print(f"  [PULL] {sym:<6} requesting SIP feed...")
    try:
        req = StockBarsRequest(
            symbol_or_symbols=sym,
            timeframe=TimeFrame.Minute,
            start=START,
            end=END,
            feed="sip"
        )
        bars = client.get_stock_bars(req)
        df = bars.df

        if df.empty:
            print(f"  [WARN] No data returned for {sym}")
            continue

        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(sym, level=0)

        df = df.reset_index()
        df = df.rename(columns={"timestamp": "time"})
        df["time"] = pd.to_datetime(df["time"], utc=True)

        # RTH session filter strictly 09:30 to 16:00 ET
        df["time_et"] = df["time"].dt.tz_convert("America/New_York")
        df = df[(df["time_et"].dt.time >= dt.time(9, 30)) & (df["time_et"].dt.time < dt.time(16, 0))].copy()
        df = df.drop(columns=["time_et"])

        df.to_parquet(out_file, compression="snappy", index=False)
        print(f"  [SAVED] {sym:<6} -> {len(df):,} RTH 1-min bars")
    except Exception as e:
        print(f"  [FAIL] {sym}: {e}")

print("=" * 80)
print("PULL COMPLETE")
print("=" * 80)
