#!/usr/bin/env python3
"""
CANONICAL ALPACA 1-MIN SIP DATA HARVESTER (2017 - 2026)
Features:
  - Resumable symbol-level chunking
  - Strict Regular Market Hours (09:30 <= ET < 16:00)
  - PyArrow / Snappy compression
  - Rate-limit aware pagination
"""

import os
import sys
import time
from pathlib import Path
import datetime as dt
import requests
import pandas as pd

# Load environment credentials
for env_p in [Path("/home/moonrider1968/bots/.env"), Path("/home/moonrider1968/.env")]:
    if env_p.exists():
        for line in env_p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
SEC = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
assert KEY and SEC, "Alpaca API credentials missing."

HEADERS = {
    "APCA-API-KEY-ID": KEY,
    "APCA-API-SECRET-KEY": SEC,
    "Accept": "application/json"
}

URL = "https://data.alpaca.markets/v2/stocks/bars"
DATA_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA", "AVGO",
    "GOOGL", "META", "AMZN", "TSLA",
    "JPM", "GS", "XOM", "CVX",
    "WMT", "COST", "CAT", "GE"
]

# Development + Validation + OOS Window: 2017-01-03 to 2026-08-31
START_DATE = "2017-01-03T09:00:00Z"
END_DATE = "2026-08-31T21:00:00Z"

print("=" * 75)
print("CANONICAL SIP 1-MIN HARVESTER — INITIALIZING")
print(f"Store Path:  {DATA_DIR}")
print(f"Symbols:     {len(SYMBOLS)} frozen equities")
print(f"Time Range:  {START_DATE[:10]} -> {END_DATE[:10]}")
print("=" * 75)

for sym in SYMBOLS:
    out_file = DATA_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
    if out_file.exists():
        print(f"[{sym}] Target file already exists. Skipping to avoid re-download.")
        continue

    print(f"\n>>> HARVESTING {sym} (Paginating 1-Min SIP)...")
    all_chunks = []
    page_token = None
    total_bars = 0
    t0 = time.time()

    while True:
        params = {
            "symbols": sym,
            "timeframe": "1Min",
            "start": START_DATE,
            "end": END_DATE,
            "adjustment": "all",
            "feed": "sip",
            "limit": 10000
        }
        if page_token:
            params["page_token"] = page_token

        try:
            r = requests.get(URL, headers=HEADERS, params=params, timeout=30)
            if r.status_code == 429:
                print(" [Rate-Limit 429: Sleeping 10s] ", end="", flush=True)
                time.sleep(10)
                continue
            if not r.ok:
                print(f"\nAPI Error {r.status_code}: {r.text[:200]}")
                break

            res = r.json()
            raw_bars = res.get("bars", {}).get(sym, [])
            if not raw_bars:
                break

            df_chunk = pd.DataFrame(raw_bars)
            df_chunk["time"] = pd.to_datetime(df_chunk["t"], format="ISO8601", utc=True)
            df_chunk = df_chunk.rename(columns={
                "o": "open", "h": "high", "l": "low", 
                "c": "close", "v": "volume", "vw": "vwap", "n": "trades"
            })

            # Strictly enforce 09:30 <= ET < 16:00
            df_chunk["time_et"] = df_chunk["time"].dt.tz_convert("America/New_York")
            rth_mask = (df_chunk["time_et"].dt.time >= dt.time(9, 30)) & (df_chunk["time_et"].dt.time < dt.time(16, 0))
            df_chunk = df_chunk[rth_mask][["time", "open", "high", "low", "close", "volume", "vwap", "trades"]]

            if not df_chunk.empty:
                all_chunks.append(df_chunk)
                total_bars += len(df_chunk)
                print(f"\r  Accumulated {total_bars:,} RTH bars...", end="", flush=True)

            page_token = res.get("next_page_token")
            if not page_token:
                break

        except Exception as e:
            print(f"\nException on {sym}: {e}")
            break

    if all_chunks:
        full_df = pd.concat(all_chunks, ignore_index=True)
        full_df = full_df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
        full_df.to_parquet(out_file, engine="pyarrow", compression="snappy", index=False)
        dur = time.time() - t0
        print(f"\n  [SAVED] {sym}: {len(full_df):,} RTH bars in {dur:.1f}s -> {out_file.name}")
    else:
        print(f"\n  [WARN] {sym}: Zero bars retrieved.")

print("\n" + "=" * 75)
print("CANONICAL DATASET HARVEST COMPLETE")
print("=" * 75)
