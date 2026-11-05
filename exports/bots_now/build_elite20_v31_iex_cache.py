#!/usr/bin/env python3

import os
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

BASE = Path("/home/moonrider1968/bots")
OUT = BASE / "data" / "elite20_v31"
OUT.mkdir(parents=True, exist_ok=True)

for envfile in [
    Path("/home/moonrider1968/.env"),
    BASE / ".env",
]:
    if envfile.exists():
        load_dotenv(envfile, override=False)

KEY = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
SECRET = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")

if not KEY or not SECRET:
    raise SystemExit("FATAL: Alpaca API credentials missing from environment.")

SYMBOLS = [
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA", "AVGO",
    "GOOGL", "META", "NFLX",
    "WMT", "COST", "PG",
    "XOM", "CVX", "COP",
    "AMZN",
    "CAT", "GE",
    "JPM", "GS",
]

CACHE_START = "2021-01-01"
EVAL_START = "2021-09-01"
END_DATE = "2026-09-04"

URL = "https://data.alpaca.markets/v2/stocks/bars"

HEADERS = {
    "APCA-API-KEY-ID": KEY,
    "APCA-API-SECRET-KEY": SECRET,
}

manifest = {
    "frozen_timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "source": "Alpaca_API_v2",
    "feed": "iex",
    "adjustment": "all",
    "cache_start": CACHE_START,
    "evaluation_start": EVAL_START,
    "end_date": END_DATE,
    "symbols": [],
}

print("=" * 110)
print("ELITE20 V3.1 PURE-IEX WARMUP CACHE")
print("=" * 110)
print(f"Cache period:      {CACHE_START} -> {END_DATE}")
print(f"Evaluation period: {EVAL_START} -> {END_DATE}")
print("Feed:              iex")
print("Adjustment:        all")
print("=" * 110)

for n, sym in enumerate(SYMBOLS, 1):
    print(f"[{n:02d}/{len(SYMBOLS)}] {sym}: fetching...", flush=True)

    rows = []
    token = None
    pages = 0

    while True:
        params = {
            "symbols": sym,
            "timeframe": "1Day",
            "start": f"{CACHE_START}T00:00:00Z",
            "end": f"{END_DATE}T23:59:59Z",
            "limit": 1000,
            "adjustment": "all",
            "feed": "iex",
            "sort": "asc",
        }

        if token:
            params["page_token"] = token

        r = requests.get(
            URL,
            headers=HEADERS,
            params=params,
            timeout=30,
        )

        if r.status_code != 200:
            raise RuntimeError(
                f"{sym}: Alpaca HTTP {r.status_code}: {r.text[:500]}"
            )

        payload = r.json()
        pages += 1

        batch = payload.get("bars", {}).get(sym, [])
        rows.extend(batch)

        token = payload.get("next_page_token")
        if not token:
            break

        time.sleep(0.15)

    if not rows:
        raise RuntimeError(f"{sym}: zero IEX bars returned.")

    df = pd.DataFrame(rows)

    required = {"t", "o", "h", "l", "c", "v"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        raise RuntimeError(
            f"{sym}: missing Alpaca columns: {sorted(missing_cols)}"
        )

    df = df.rename(columns={
        "t": "timestamp",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "n": "trade_count",
        "vw": "vwap",
    })

    ts = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = ts.dt.tz_convert("America/New_York").dt.date

    keep = [
        c for c in
        [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "vwap",
        ]
        if c in df.columns
    ]

    df = df[keep].copy()
    df = df.drop_duplicates(subset=["date"], keep="last")
    df = df.sort_values("date").reset_index(drop=True)

    path = OUT / f"{sym}.csv"
    df.to_csv(path, index=False)

    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    dates = pd.to_datetime(df["date"])

    pre_eval_bars = int((dates < pd.Timestamp(EVAL_START)).sum())
    eval_bars = int((dates >= pd.Timestamp(EVAL_START)).sum())

    rec = {
        "symbol": sym,
        "source": "Alpaca_API_v2",
        "feed": "iex",
        "adjustment": "all",
        "bars": int(len(df)),
        "pre_eval_warmup_bars": pre_eval_bars,
        "evaluation_bars": eval_bars,
        "start_date": str(dates.min().date()),
        "end_date": str(dates.max().date()),
        "pages": pages,
        "sha256": sha,
    }

    manifest["symbols"].append(rec)

    print(
        f"       bars={len(df):4d} | "
        f"warmup={pre_eval_bars:3d} | "
        f"eval={eval_bars:4d} | "
        f"{rec['start_date']} -> {rec['end_date']} | "
        f"pages={pages}"
    )

manifest_path = OUT / "manifest.json"
manifest_path.write_text(
    json.dumps(manifest, indent=2) + "\n",
    encoding="utf-8",
)

print()
print("=" * 110)
print("V3.1 CACHE COMPLETE")
print("=" * 110)

print(
    f"{'SYMBOL':<7} "
    f"{'BARS':>5} "
    f"{'WARMUP':>7} "
    f"{'EVAL':>5} "
    f"{'START':>12} "
    f"{'END':>12}"
)

print("-" * 65)

for r in manifest["symbols"]:
    print(
        f"{r['symbol']:<7} "
        f"{r['bars']:>5} "
        f"{r['pre_eval_warmup_bars']:>7} "
        f"{r['evaluation_bars']:>5} "
        f"{r['start_date']:>12} "
        f"{r['end_date']:>12}"
    )

print()
print("Manifest:", manifest_path)
print("Old V3 cache was NOT modified.")
