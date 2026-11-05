#!/usr/bin/env python3
"""
Elite20 V3: Pure Alpaca Bar Cache & Manifest Freezer
---------------------------------------------------
- Zero third-party dependencies (strictly Alpaca /v2/stocks/bars).
- Pulls pure IEX feed, adjustment=all.
- Validates missing trading days against Alpaca official calendar.
- Generates immutable SHA-256 manifest.
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
import requests
import pandas as pd

BASE_DIR = Path("/home/moonrider1968/bots")
DATA_DIR = BASE_DIR / "data" / "elite20_v3"
DATA_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
DATA_URL = "https://data.alpaca.markets/v2"
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

SYMBOLS = [
    "AAPL", "AMZN", "AVGO", "CAT", "COP", "COST", "CVX", "GE",
    "GOOGL", "GS", "JPM", "META", "MSFT", "NFLX", "NVDA", "PG",
    "QQQ", "SPY", "WMT", "XOM"
]

START_DATE = "2021-09-01"
END_DATE = "2026-09-04"

def get_official_calendar() -> set:
    resp = requests.get(f"{BASE_URL}/calendar", headers=HEADERS, params={"start": START_DATE, "end": END_DATE}, timeout=15)
    resp.raise_for_status()
    return {c["date"] for c in resp.json()}

def fetch_symbol(symbol: str) -> pd.DataFrame:
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    page_token = None
    all_bars = []
    while True:
        params = {
            "timeframe": "1Day",
            "start": START_DATE,
            "end": END_DATE,
            "limit": 1000,
            "adjustment": "all",
            "feed": "iex"
        }
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        bars = data.get("bars", [])
        if bars:
            all_bars.extend(bars)
        page_token = data.get("next_page_token")
        if not page_token:
            break

    recs = [{
        "date": pd.to_datetime(b["t"]).tz_convert("America/New_York").strftime("%Y-%m-%d"),
        "open": float(b["o"]),
        "high": float(b["h"]),
        "low": float(b["l"]),
        "close": float(b["c"]),
        "volume": int(b["v"])
    } for b in all_bars]
    df = pd.DataFrame(recs).drop_duplicates("date").sort_values("date")
    return df

def run_cache():
    print(f"📡 Fetching official NYSE trading calendar ({START_DATE} to {END_DATE})...")
    official_sessions = get_official_calendar()
    print(f"Official NYSE Sessions count: {len(official_sessions)}")

    manifest = []
    
    for sym in SYMBOLS:
        print(f"📥 Pulling & caching {sym}...")
        df = fetch_symbol(sym)
        out_csv = DATA_DIR / f"{sym}.csv"
        df.to_csv(out_csv, index=False)

        # File sha256
        sha256 = hashlib.sha256(out_csv.read_bytes()).hexdigest()
        
        # Missing sessions
        actual_sessions = set(df["date"].tolist())
        missing_sessions = sorted(list(official_sessions - actual_sessions))
        
        manifest.append({
            "symbol": sym,
            "feed": "iex",
            "source": "Alpaca_API_v2",
            "bars": len(df),
            "start_date": df["date"].iloc[0],
            "end_date": df["date"].iloc[-1],
            "missing_session_count": len(missing_sessions),
            "missing_sessions": missing_sessions,
            "sha256": sha256
        })

    # Save manifest
    m_file = DATA_DIR / "manifest.json"
    with open(m_file, "w") as f:
        json.dump({
            "frozen_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "feed": "iex",
            "nyse_official_sessions": len(official_sessions),
            "symbols": manifest
        }, f, indent=2)

    print("\n" + "=" * 80)
    print("DATA MANIFEST FROZEN")
    print("=" * 80)
    m_df = pd.DataFrame(manifest)[["symbol", "bars", "start_date", "end_date", "missing_session_count", "sha256"]]
    print(m_df.to_string(index=False))
    print(f"\nSaved manifest to {m_file}")

if __name__ == "__main__":
    run_cache()
