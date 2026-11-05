#!/usr/bin/env python3
"""
Kraken 00:00 UTC Historical Downloader & Manifest Auditor (Audited N=30 Universe)
Enforces strictly completed daily bars (purging in-flight UTC candles),
accurately tracks raw API ceiling limits, and logs data health.
"""

import os
import time
import pandas as pd
import numpy as np
import ccxt

DATA_DIR = "/home/moonrider1968/bots/crypto_data"

CERTIFIED_CRYPTO_UNIVERSE = [
    'BTC/USD', 'ETH/USD', 'XRP/USD', 'SOL/USD', 'ADA/USD',
    'ZEC/USD', 'HYPE/USD', 'XMR/USD', 'TAO/USD', 'DOGE/USD',
    'SUI/USD', 'LINK/USD', 'PUMP/USD', 'XLM/USD', 'AVAX/USD',
    'LTC/USD', 'UNI/USD', 'CRV/USD', 'CC/USD', 'ONDO/USD',
    'NEAR/USD', 'BNB/USD', 'AAVE/USD', 'AKE/USD', 'TRX/USD',
    'PEPE/USD', 'ICP/USD', 'ENA/USD', 'VVV/USD', 'FET/USD'
]

def main():
    print("=" * 105)
    print(f" 📥 DOWNLOADING AUDITED KRAKEN DATASET & GENERATING MANIFEST (N={len(CERTIFIED_CRYPTO_UNIVERSE)})")
    print("=" * 105)

    exchange = ccxt.kraken({"enableRateLimit": True, "timeout": 30000})
    manifest_records = []

    for idx, symbol in enumerate(CERTIFIED_CRYPTO_UNIVERSE, 1):
        clean_name = symbol.replace("/", "_")
        csv_path = os.path.join(DATA_DIR, f"{clean_name}_1d.csv")
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=720)
            if not ohlcv:
                print(f"[{idx:02d}/30] ❌ FAILED: {symbol} returned empty dataset.")
                continue

            raw_api_bars = len(ohlcv)

            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)

            # Keep only completely closed 00:00 UTC daily sessions.
            current_utc_day = pd.Timestamp.now(tz="UTC").normalize()

            before_filter = len(df)
            df = df[df.index < current_utc_day].copy()
            dropped_incomplete_bars = before_filter - len(df)

            if df.empty:
                raise RuntimeError(f"{symbol}: no completed daily bars remain")

            df["quote_volume"] = df["close"] * df["volume"]
            median_vol_30d = df.tail(30)["quote_volume"].median()

            start_dt = df.index[0]
            end_dt = df.index[-1]
            total_bars = len(df)

            expected_days = (end_dt - start_dt).days + 1
            missing_bars = max(0, expected_days - total_bars)

            # Detect API truncation from the RAW response, not post-filter count.
            hit_ceiling = raw_api_bars >= 720

            df.to_csv(csv_path)

            manifest_records.append({
                "rank_idx": idx,
                "symbol": symbol,
                "base": symbol.split("/")[0],
                "raw_api_bars": raw_api_bars,
                "completed_bars": total_bars,
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": end_dt.strftime("%Y-%m-%d"),
                "missing_bars": missing_bars,
                "dropped_incomplete_bars": dropped_incomplete_bars,
                "hit_720_ceiling": hit_ceiling,
                "median_daily_turnover_30d_usd": round(median_vol_30d, 2),
            })

            ceiling_tag = "[KRAKEN CEILING]" if hit_ceiling else f"[ACTUAL {total_bars}d]"

            print(
                f"[{idx:02d}/30] ✅ {symbol:<10} | "
                f"{total_bars:>3} completed bars | "
                f"{start_dt:%Y-%m-%d} to {end_dt:%Y-%m-%d} | "
                f"Missing: {missing_bars} | "
                f"Dropped incomplete: {dropped_incomplete_bars} | "
                f"{ceiling_tag}"
            )
            time.sleep(0.06)

        except Exception as e:
            print(f"[{idx:02d}/30] ❌ {symbol}: {e}")

    df_manifest = pd.DataFrame(manifest_records)
    manifest_csv = os.path.join(DATA_DIR, "universe_manifest.csv")
    df_manifest.to_csv(manifest_csv, index=False)

    print("\n" + "=" * 105)
    print(f" 📋 DATASET MANIFEST SUMMARY ({manifest_csv})")
    print("=" * 105)
    print(f"Total Symbols Ingested: {len(df_manifest)}")
    print(f"Symbols hitting 720 raw ceiling: {df_manifest['hit_720_ceiling'].sum()} / {len(df_manifest)}")
    print(f"Symbols with internal date gaps: {(df_manifest['missing_bars'] > 0).sum()}")
    print("=" * 105)

if __name__ == "__main__":
    main()
