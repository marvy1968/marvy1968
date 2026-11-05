#!/usr/bin/env python3
"""
ALPACA SIP INTRADAY DATA HARVESTER
Paginates and caches high-density 1-Min SIP data into compressed local Parquet files.
"""

import os
import sys
import time
from pathlib import Path
import datetime as dt
import requests
import pandas as pd

# Load environment
for p in [Path("/home/moonrider1968/bots/.env"), Path("/home/moonrider1968/.env")]:
    if p.exists():
        for line in p.read_text().splitlines():
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

DATA_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA", "AVGO",
    "GOOGL", "META", "AMZN", "TSLA",
    "JPM", "GS", "XOM", "CVX",
    "WMT", "COST", "CAT", "GE"
]

START_DATE = "2024-01-02T00:00:00Z"
END_DATE = "2024-06-28T23:59:59Z"
URL = "https://data.alpaca.markets/v2/stocks/bars"

print("=" * 80)
print("DOWNLOADING 6-MONTH SIP 1-MIN CANONICAL DATASET (Q1-Q2 2024)")
print(f"Target Directory: {DATA_DIR}")
print(f"Symbols ({len(SYMBOLS)}): {', '.join(SYMBOLS)}")
print("=" * 80)

for sym in SYMBOLS:
    out_file = DATA_DIR / f"{sym}_1M_2024_H1.parquet"
    if out_file.exists():
        print(f"  [EXISTS] {sym:<6} -> {out_file.name}")
        continue

    print(f"  [FETCHING] {sym}...", end="", flush=True)
    all_bars = []
    page_token = None
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
            if not r.ok:
                print(f" Error {r.status_code}: {r.text[:100]}")
                break
            
            data = r.json()
            bars = data.get("bars", {}).get(sym, [])
            if not bars:
                break
            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token:
                break
        except Exception as e:
            print(f" Exception: {e}")
            break

    if all_bars:
        df = pd.DataFrame(all_bars)
        df["time"] = pd.to_datetime(df["t"], utc=True)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "vwap", "n": "trades"})
        df = df.sort_values("time").reset_index(drop=True)
        
        # Filter strictly to RTH (09:30 to 16:00 ET)
        df["time_et"] = df["time"].dt.tz_convert("America/New_York")
        df_rth = df[(df["time_et"].dt.time >= dt.time(9, 30)) & (df["time_et"].dt.time <= dt.time(16, 0))].copy()
        df_rth.to_parquet(out_file, index=False, compression="snappy")
        
        elapsed = time.time() - t0
        print(f" Done. {len(df_rth):,} RTH bars saved ({elapsed:.1f}s)")
    else:
        print(" Failed: Zero bars retrieved.")

print("\nIngestion Complete. All symbols cached locally.")
