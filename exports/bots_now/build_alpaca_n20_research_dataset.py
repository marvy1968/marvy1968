#!/usr/bin/env python3

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np
from dotenv import load_dotenv

# ------------------------------------------------------------
# PATHS
# ------------------------------------------------------------
ROOT = Path("/home/moonrider1968/bots")
ALPACA_BOT = ROOT / "alpaca_crypto_roi_bot"
OUT = ROOT / "alpaca_n20_research"
DATA_DIR = OUT / "data"
REPORT_DIR = OUT / "reports"

DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Use known Alpaca environment without exposing credentials.
load_dotenv(ALPACA_BOT / ".env", override=False)

api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
api_secret = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")

if not api_key or not api_secret:
    raise SystemExit("FAIL: Alpaca credentials not found.")

# Prefer the existing Alpaca venv when this script is executed there.
try:
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    raise SystemExit(
        "FAIL: alpaca-py unavailable in current interpreter.\n"
        "Run this script with alpaca_crypto_roi_bot/.venv/bin/python3"
    )

# ------------------------------------------------------------
# FROZEN BROKER UNIVERSE
# ------------------------------------------------------------
ALPACA_N20 = [
    "AAVE/USD",
    "ADA/USD",
    "AVAX/USD",
    "BAT/USD",
    "BCH/USD",
    "BTC/USD",
    "CRV/USD",
    "DOGE/USD",
    "DOT/USD",
    "ETH/USD",
    "GRT/USD",
    "LINK/USD",
    "LTC/USD",
    "ONDO/USD",
    "PEPE/USD",
    "POL/USD",
    "SHIB/USD",
    "SOL/USD",
    "UNI/USD",
    "XRP/USD",
]

# Exact canonical historical interval.
START = pd.Timestamp("2024-09-08 00:00:00", tz="UTC")
LAST_COMPLETED = pd.Timestamp("2026-08-27 00:00:00", tz="UTC")

# Request one day beyond final desired bar, then explicitly filter.
REQUEST_END = pd.Timestamp("2026-08-28 00:00:00", tz="UTC")

EXPECTED_DAYS = pd.date_range(
    START,
    LAST_COMPLETED,
    freq="D",
    tz="UTC"
)

print("=" * 125)
print(" 🏗️ ALPACA_N20 BROKER-NATIVE RESEARCH DATASET BUILDER")
print(" Separate branch | NO CORE_N27 modification | NO ORDERS")
print("=" * 125)
print(f"Target interval : {START.date()} -> {LAST_COMPLETED.date()}")
print(f"Calendar days   : {len(EXPECTED_DAYS)}")
print(f"Universe        : {len(ALPACA_N20)} assets")
print(f"Output          : {OUT}")

client = CryptoHistoricalDataClient(api_key, api_secret)

manifest_rows = []
failures = []

# ------------------------------------------------------------
# FETCH EACH SYMBOL INDEPENDENTLY
# ------------------------------------------------------------
for rank_idx, sym in enumerate(ALPACA_N20, start=1):

    print("\n" + "-" * 125)
    print(f"[{rank_idx:02d}/{len(ALPACA_N20)}] {sym}")
    print("-" * 125)

    try:
        req = CryptoBarsRequest(
            symbol_or_symbols=[sym],
            timeframe=TimeFrame.Day,
            start=START.to_pydatetime(),
            end=REQUEST_END.to_pydatetime(),
        )

        bars = client.get_crypto_bars(req).df

        if bars is None or bars.empty:
            raise RuntimeError("Alpaca returned no bars.")

        # Alpaca returns a symbol/timestamp MultiIndex.
        try:
            df = bars.loc[sym].copy()
        except Exception:
            compact = sym.replace("/", "")
            try:
                df = bars.loc[compact].copy()
            except Exception:
                raise RuntimeError(
                    f"Unable to isolate {sym} from returned dataframe. "
                    f"Index names={bars.index.names}"
                )

        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "datetime"
        df.sort_index(inplace=True)

        # Remove duplicate timestamps deterministically.
        dup_count = int(df.index.duplicated(keep="last").sum())
        if dup_count:
            df = df[~df.index.duplicated(keep="last")]

        raw_api_bars = len(df)

        # ----------------------------------------------------
        # EXACT FROZEN WINDOW ONLY
        # ----------------------------------------------------
        df = df[
            (df.index >= START) &
            (df.index <= LAST_COMPLETED)
        ].copy()

        # Keep canonical OHLCV columns.
        required = ["open", "high", "low", "close", "volume"]

        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            raise RuntimeError(
                f"Missing required Alpaca columns: {missing_cols}"
            )

        df = df[required].copy()

        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Reject malformed bars.
        malformed = (
            df[required].isna().any(axis=1)
            | (df["open"] <= 0)
            | (df["high"] <= 0)
            | (df["low"] <= 0)
            | (df["close"] <= 0)
            | (df["volume"] < 0)
            | (df["high"] < df["low"])
            | (df["high"] < df[["open", "close"]].max(axis=1))
            | (df["low"] > df[["open", "close"]].min(axis=1))
        )

        malformed_count = int(malformed.sum())

        if malformed_count:
            print(f"WARNING: malformed bars dropped = {malformed_count}")
            df = df.loc[~malformed].copy()

        # Canonical derived raw fields.
        df.insert(
            0,
            "timestamp",
            (df.index.astype("int64") // 1_000_000).astype("int64")
        )

        # Match engine semantics exactly.
        df["quote_volume"] = df["close"] * df["volume"]

        # ----------------------------------------------------
        # COVERAGE AUDIT
        # ----------------------------------------------------
        actual_dates = pd.DatetimeIndex(df.index.normalize().unique())

        missing_dates = EXPECTED_DAYS.difference(actual_dates)

        completed_bars = len(df)

        start_date = df.index.min() if completed_bars else pd.NaT
        end_date = df.index.max() if completed_bars else pd.NaT

        # Same liquidity statistic used by research architecture.
        med30 = (
            df["quote_volume"]
            .rolling(30, min_periods=30)
            .median()
        )

        latest_med_turnover = (
            float(med30.iloc[-1])
            if len(med30) and pd.notna(med30.iloc[-1])
            else np.nan
        )

        # ----------------------------------------------------
        # WRITE CANONICAL RAW CSV
        # ----------------------------------------------------
        clean = sym.replace("/", "_")
        output_file = DATA_DIR / f"{clean}_1d.csv"

        export = df.reset_index()

        export = export[
            [
                "datetime",
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
            ]
        ]

        export.to_csv(output_file, index=False)

        digest = hashlib.sha256(output_file.read_bytes()).hexdigest()

        print(f"Raw API bars         : {raw_api_bars}")
        print(f"Frozen-window bars   : {completed_bars}")
        print(f"Start                : {start_date}")
        print(f"End                  : {end_date}")
        print(f"Missing calendar days: {len(missing_dates)}")
        print(f"Duplicates removed   : {dup_count}")
        print(f"Malformed removed    : {malformed_count}")
        print(f"SHA256               : {digest}")
        print(f"Wrote                : {output_file}")

        if len(missing_dates):
            preview = ", ".join(
                str(x.date()) for x in missing_dates[:10]
            )
            print(f"Missing preview      : {preview}")

        manifest_rows.append({
            "rank_idx": rank_idx,
            "symbol": sym,
            "base": sym.split("/")[0],
            "raw_api_bars": raw_api_bars,
            "completed_bars": completed_bars,
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
            "missing_bars": len(missing_dates),
            "dropped_incomplete_bars": max(
                raw_api_bars - completed_bars,
                0
            ),
            "hit_720_ceiling": raw_api_bars >= 720,
            "median_daily_turnover_30d_usd": latest_med_turnover,
            "duplicate_bars_removed": dup_count,
            "malformed_bars_removed": malformed_count,
            "sha256": digest,
        })

    except Exception as e:
        failures.append((sym, type(e).__name__, str(e)))
        print(
            f"FAIL: {sym} | "
            f"{type(e).__name__}: {e}"
        )

# ------------------------------------------------------------
# WRITE MANIFEST
# ------------------------------------------------------------
manifest = pd.DataFrame(manifest_rows)

manifest_path = OUT / "alpaca_n20_manifest.csv"
manifest.to_csv(manifest_path, index=False)

# ------------------------------------------------------------
# VALIDATION SUMMARY
# ------------------------------------------------------------
print("\n" + "=" * 125)
print(" DATASET VALIDATION SUMMARY")
print("=" * 125)

if not manifest.empty:
    display_cols = [
        "symbol",
        "completed_bars",
        "start_date",
        "end_date",
        "missing_bars",
        "median_daily_turnover_30d_usd",
    ]

    print(
        manifest[display_cols]
        .to_string(index=False)
    )

print("\n" + "-" * 125)

print(f"Successful assets : {len(manifest_rows)}/{len(ALPACA_N20)}")
print(f"Failures          : {len(failures)}")

if failures:
    print("\nFAILURES:")
    for sym, typ, msg in failures:
        print(f"  {sym}: {typ}: {msg}")

full_window = []

if not manifest.empty:
    full_window = manifest[
        (manifest["start_date"] == "2024-09-08") &
        (manifest["end_date"] == "2026-08-27") &
        (manifest["missing_bars"] == 0)
    ]["symbol"].tolist()

print(
    f"\nExact full-window assets: "
    f"{len(full_window)}/{len(ALPACA_N20)}"
)

if len(full_window) != len(ALPACA_N20):
    incomplete = [
        x for x in ALPACA_N20
        if x not in full_window
    ]

    print(
        "Incomplete-history assets: "
        + ", ".join(incomplete)
    )

# Metadata lock file.
metadata = {
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "universe": ALPACA_N20,
    "start": START.isoformat(),
    "last_completed": LAST_COMPLETED.isoformat(),
    "source": "Alpaca CryptoHistoricalDataClient",
    "timeframe": "1Day",
    "core_n27_modified": False,
}

with open(OUT / "dataset_metadata.json", "w") as f:
    json.dump(metadata, f, indent=2)

print("\n" + "=" * 125)

if (
    len(manifest_rows) == 20
    and len(full_window) == 20
    and not failures
):
    print(
        " ✅ DATASET BUILD PASS — "
        "20/20 COMPLETE ALPACA-NATIVE HISTORIES"
    )
else:
    print(
        " ⚠️ DATASET BUILD INCOMPLETE — "
        "DO NOT RUN CERTIFICATION YET"
    )

print(" CORE_N27 RESEARCH DATA WAS NOT MODIFIED")
print(" NO ORDERS WERE SUBMITTED")
print("=" * 125)
