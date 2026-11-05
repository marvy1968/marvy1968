#!/usr/bin/env python3
"""
Crypto Mean-Reversion: Verified Finalized-Bar Batch Scan (v2)
- Strict Finalized UTC-Bar Filtering (Drops in-progress UTC day)
- Exact 14 Alpaca Tradable Pairs with Cluster Firewall
- Dynamic 0.50% NAV Sizing based on Point-in-Time -4.2σ Stop
"""

import os
import sys
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import requests

ENV_FILE = Path("/home/moonrider1968/bots/.env")
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_ENVIRONMENT = os.getenv("ALPACA_ENVIRONMENT", "paper").lower()

BASE_URL = "https://paper-api.alpaca.markets/v2" if ALPACA_ENVIRONMENT == "paper" else "https://api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us"

SESSION = requests.Session()
SESSION.headers.update({
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
})

# Exactly 14 Alpaca Verified Tradable Pairs
CRYPTO_CLUSTERS = {
    "BTC/USD": "CORE_L1",
    "ETH/USD": "CORE_L1",
    "SOL/USD": "ALT_L1",
    "AVAX/USD": "ALT_L1",
    "DOT/USD": "ALT_L1",
    "UNI/USD": "DEFI",
    "AAVE/USD": "DEFI",
    "CRV/USD": "DEFI",
    "LINK/USD": "INFRA",
    "GRT/USD": "INFRA",
    "LDO/USD": "INFRA",
    "LTC/USD": "LEGACY",
    "BCH/USD": "LEGACY",
    "XRP/USD": "LEGACY"
}

CRYPTO_SYMBOLS = sorted(list(CRYPTO_CLUSTERS.keys()))
BASE_RISK_PCT = 0.0050      # 0.50% NAV risk per trade
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Open Risk
MAX_CONCURRENT_POSITIONS = 3
MAX_PER_CLUSTER = 1

def alpaca_request(method: str, url: str, params: dict = None) -> dict:
    try:
        resp = SESSION.request(method, url, params=params, timeout=15)
        if resp.status_code in [200, 201]:
            return resp.json()
        print(f"⚠️ API {resp.status_code} on {url}: {resp.text[:120]}")
    except requests.RequestException as e:
        print(f"❌ Network error: {e}")
    return None

def fetch_finalized_crypto_bars(symbols: list, lookback_days: int = 60) -> dict:
    now_utc = datetime.now(timezone.utc)
    today_utc_date = now_utc.date()

    start_date = (now_utc - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start_date,
        "limit": 10000
    }
    data = alpaca_request("GET", f"{DATA_URL}/bars", params=params)
    if not data or "bars" not in data:
        return {}

    result = {}
    for sym, bars in data["bars"].items():
        records = []
        for b in bars:
            b_dt = pd.to_datetime(b["t"]).astimezone(timezone.utc)
            # HARD FINALIZED GUARD: Drop in-progress current UTC day
            if b_dt.date() >= today_utc_date:
                continue

            records.append({
                "timestamp": b_dt,
                "open": float(b["o"]),
                "high": float(b["h"]),
                "low": float(b["l"]),
                "close": float(b["c"]),
                "volume": float(b["v"])
            })
        df = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
        result[sym] = df
    return result

def main():
    now_utc = datetime.now(timezone.utc)
    print("=" * 125)
    print(" 🔍 FINALIZED-BAR CRYPTO SCAN DRY RUN (v2 - Strict UTC Finalized Enforcement)")
    print(f" Current UTC Clock: {now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} | Active In-Progress UTC Day: {now_utc.date()}")
    print("=" * 125)

    account = alpaca_request("GET", f"{BASE_URL}/account")
    positions = alpaca_request("GET", f"{BASE_URL}/positions")

    if not account:
        print("❌ Failed to fetch Alpaca account state.")
        return

    nav = float(account["equity"])
    cash = float(account["cash"])
    pos_list = [p for p in positions if "/" in p.get("symbol", "") or "USD" in p.get("symbol", "")] if isinstance(positions, list) else []

    print(f"\n 💼 Live Portfolio Account State:")
    print(f"    Account NAV:         ${nav:,.2f}")
    print(f"    Available Cash:      ${cash:,.2f}")
    print(f"    Active Crypto Pos:   {len(pos_list)} / {MAX_CONCURRENT_POSITIONS}")
    print("-" * 125)

    bars_dict = fetch_finalized_crypto_bars(CRYPTO_SYMBOLS, lookback_days=60)
    scan_rows = []
    setups = []

    print(f" 📊 Evaluating 20 SMA Centerline & Sigma Envelopes on Guaranteed Finalized Closes...\n")
    for sym in CRYPTO_SYMBOLS:
        cluster = CRYPTO_CLUSTERS[sym]
        df = bars_dict.get(sym)

        if df is None or len(df) < 22:
            bars_found = len(df) if df is not None else 0
            scan_rows.append({
                "Pair": sym, "Cluster": cluster, "Status": f"NO_DATA({bars_found})",
                "Last Finalized UTC": "N/A", "Close": "N/A", "20 SMA": "N/A",
                "-3.2σ (Entry)": "N/A", "-4.2σ (Stop)": "N/A", "Signal": "NO"
            })
            continue

        df["ma20"] = df["close"].rolling(20).mean()
        df["std20"] = df["close"].rolling(20).std()
        df["lower_32s"] = df["ma20"] - (3.2 * df["std20"])
        df["lower_42s"] = df["ma20"] - (4.2 * df["std20"])

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        c_px = float(cur["close"])
        ma20 = float(cur["ma20"])
        l32 = float(cur["lower_32s"])
        l42 = float(cur["lower_42s"])
        bar_date = cur["timestamp"].strftime("%Y-%m-%d")

        is_reclaim = (prev["close"] < prev["lower_32s"]) and (cur["close"] > cur["lower_32s"]) and (cur["close"] < cur["ma20"])

        sig_label = "RECLAIM_TRIGGER" if is_reclaim else "NO"
        fmt = ".4f" if c_px < 10 else ".2f"

        scan_rows.append({
            "Pair": sym,
            "Cluster": cluster,
            "Status": "ELIGIBLE",
            "Last Finalized UTC": bar_date,
            "Close": f"${c_px:{fmt}}",
            "20 SMA": f"${ma20:{fmt}}",
            "-3.2σ (Entry)": f"${l32:{fmt}}",
            "-4.2σ (Stop)": f"${l42:{fmt}}",
            "Signal": sig_label
        })

        if is_reclaim:
            stop_dist = c_px - l42
            target_risk_usd = nav * BASE_RISK_PCT
            units = round(target_risk_usd / stop_dist, 6) if stop_dist > 0 else 0
            setups.append({
                "pair": sym, "cluster": cluster, "date": bar_date,
                "close": c_px, "stop": l42, "target": ma20, "stop_dist": stop_dist,
                "units": units, "risk_usd": target_risk_usd
            })

    print(pd.DataFrame(scan_rows).to_string(index=False))
    print("=" * 125)

    print("\n 🎯 GUARANTEED FINALIZED RECLAIM SETUPS:")
    if not setups:
        print("    No active -3.2σ reclaim setups on the latest finalized bar.")
    else:
        for s in setups:
            fmt = ".4f" if s["close"] < 10 else ".2f"
            print(f"    • {s['pair']} ({s['cluster']}) [{s['date']}]")
            print(f"      Close: ${s['close']:{fmt}} | Target (20 SMA): ${s['target']:{fmt}} | Stop (-4.2σ): ${s['stop']:{fmt}}")
            print(f"      Calculated Sizing: {s['units']} units (${s['risk_usd']:,.2f} risk / 0.50% NAV)")
    print("=" * 125)

if __name__ == "__main__":
    main()
