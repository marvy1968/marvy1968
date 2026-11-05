#!/usr/bin/env python3

import os
import time
import hashlib
from pathlib import Path

import ccxt
import pandas as pd

ROOT = Path("/home/moonrider1968/bots")
OUT = ROOT / "alpaca_exec_n20_kraken"
DATA_DIR = OUT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TARGETS = [
    "BAT/USD",
    "BCH/USD",
    "DOT/USD",
    "GRT/USD",
    "POL/USD",
    "SHIB/USD",
]

LAST_COMPLETED = pd.Timestamp("2026-08-27 00:00:00", tz="UTC")

print("=" * 120)
print(" 📥 KRAKEN BACKFILL FOR ALPACA_EXEC_N20")
print(" Separate branch | SAME PROVIDER AS CERTIFIED RESEARCH | NO ORDERS")
print("=" * 120)

exchange = ccxt.kraken({
    "enableRateLimit": True,
    "timeout": 30000,
})

# Preload markets to handle pair mappings like POL vs MATIC
try:
    exchange.load_markets()
except Exception as e:
    print(f"Warning: load_markets failed ({e}), using raw symbols")

records = []
failures = []

for idx, symbol in enumerate(TARGETS, start=1):
    print("\n" + "-" * 120)
    print(f"[{idx:02d}/{len(TARGETS)}] {symbol}")
    print("-" * 120)

    # Check pair aliases (e.g. POL/USD vs MATIC/USD on Kraken)
    fetch_sym = symbol
    if hasattr(exchange, "markets") and exchange.markets:
        if symbol not in exchange.markets and symbol == "POL/USD":
            if "MATIC/USD" in exchange.markets:
                fetch_sym = "MATIC/USD"
                print(f"Mapped {symbol} -> Kraken pair: {fetch_sym}")

    try:
        ohlcv = exchange.fetch_ohlcv(
            fetch_sym,
            timeframe="1d",
            limit=720,
        )

        if not ohlcv:
            raise RuntimeError(f"Kraken returned empty dataset for {fetch_sym}.")

        raw_api_bars = len(ohlcv)

        df = pd.DataFrame(
            ohlcv,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        df["datetime"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True,
        )

        df.set_index("datetime", inplace=True)
        df.sort_index(inplace=True)

        # Remove any in-flight bars beyond certified final date.
        before = len(df)
        df = df[df.index <= LAST_COMPLETED].copy()
        dropped_incomplete = before - len(df)

        # Match canonical raw schema.
        df["quote_volume"] = df["close"] * df["volume"]

        df = df[
            [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
            ]
        ]

        start_date = df.index.min() if len(df) else pd.NaT
        end_date = df.index.max() if len(df) else pd.NaT

        med_turnover = (
            df.tail(30)["quote_volume"].median()
            if len(df) >= 30
            else float("nan")
        )

        clean = symbol.replace("/", "_")
        out_path = DATA_DIR / f"{clean}_1d.csv"

        export = df.reset_index()
        export.to_csv(out_path, index=False)

        sha = hashlib.sha256(
            out_path.read_bytes()
        ).hexdigest()

        print(f"Raw API bars         : {raw_api_bars}")
        print(f"Completed bars       : {len(df)}")
        print(f"Start                : {start_date}")
        print(f"End                  : {end_date}")
        print(f"Dropped incomplete   : {dropped_incomplete}")
        print(f"Median turnover 30d  : {med_turnover:,.2f}")
        print(f"SHA256               : {sha}")
        print(f"Wrote                : {out_path}")

        records.append({
            "symbol": symbol,
            "raw_api_bars": raw_api_bars,
            "completed_bars": len(df),
            "start_date": (
                str(start_date.date())
                if pd.notna(start_date)
                else ""
            ),
            "end_date": (
                str(end_date.date())
                if pd.notna(end_date)
                else ""
            ),
            "dropped_incomplete_bars": dropped_incomplete,
            "hit_720_ceiling": raw_api_bars >= 720,
            "median_daily_turnover_30d_usd": med_turnover,
            "sha256": sha,
        })

    except Exception as e:
        failures.append(
            (symbol, type(e).__name__, str(e))
        )

        print(
            f"FAIL: {symbol} | "
            f"{type(e).__name__}: {e}"
        )

    time.sleep(exchange.rateLimit / 1000.0)

manifest = pd.DataFrame(records)
manifest_path = OUT / "backfill_manifest.csv"
manifest.to_csv(manifest_path, index=False)

print("\n" + "=" * 120)
print(" BACKFILL SUMMARY")
print("=" * 120)

if len(manifest):
    print(
        manifest[
            [
                "symbol",
                "completed_bars",
                "start_date",
                "end_date",
                "hit_720_ceiling",
                "median_daily_turnover_30d_usd",
            ]
        ].to_string(index=False)
    )

print()
print(f"Successful : {len(records)}/{len(TARGETS)}")
print(f"Failures   : {len(failures)}")

if failures:
    print("\nFAILURES:")
    for sym, typ, msg in failures:
        print(f"  {sym}: {typ}: {msg}")

print("\n" + "=" * 120)

if len(records) == len(TARGETS) and not failures:
    print(" ✅ KRAKEN BACKFILL COMPLETE")
else:
    print(" ⚠️ KRAKEN BACKFILL INCOMPLETE")

print(" ORIGINAL crypto_data DIRECTORY WAS NOT MODIFIED")
print(" NO ORDERS WERE SUBMITTED")
print("=" * 120)
