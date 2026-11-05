#!/usr/bin/env python3
"""
Audit Alpaca Crypto Universe
Queries Alpaca /v2/assets and market data endpoints to verify exactly which of
the 16 research symbols are tradable and have historical daily bars.
"""

import os
import sys
from pathlib import Path
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

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
}

RESEARCH_SYMBOLS = [
    "BTC", "ETH", "SOL", "AVAX", "DOT", "NEAR",
    "UNI", "AAVE", "MKR", "CRV",
    "LINK", "GRT", "LDO",
    "LTC", "BCH", "XRP"
]

print("=" * 75)
print(" 🔍 QUERYING ALPACA CRYPTO ASSETS (/v2/assets?asset_class=crypto)")
print("=" * 75)

resp = requests.get(f"{BASE_URL}/assets?asset_class=crypto&status=active", headers=HEADERS, timeout=15)
if resp.status_code != 200:
    print(f"❌ Failed to query /v2/assets: {resp.status_code} {resp.text}")
    sys.exit(1)

alpaca_assets = resp.json()
active_pairs = {a["symbol"]: a for a in alpaca_assets if a.get("tradable", False)}

print(f"Total active tradable crypto pairs on Alpaca: {len(active_pairs)}\n")

results = []
supported_pairs = []

for sym in RESEARCH_SYMBOLS:
    pair = f"{sym}/USD"
    is_tradable = pair in active_pairs
    min_order_size = active_pairs[pair].get("min_order_size") if is_tradable else "N/A"
    
    # Check if Alpaca Data feed has bars
    has_bars = False
    bar_count = 0
    if is_tradable:
        b_resp = requests.get(f"{DATA_URL}/bars", headers=HEADERS, params={"symbols": pair, "timeframe": "1Day", "limit": 10}, timeout=10)
        if b_resp.status_code == 200:
            b_data = b_resp.json()
            if "bars" in b_data and pair in b_data["bars"]:
                bar_count = len(b_data["bars"][pair])
                has_bars = bar_count > 0

    status = "SUPPORTED" if (is_tradable and has_bars) else ("NO_BARS" if is_tradable else "UNAVAILABLE")
    if status == "SUPPORTED":
        supported_pairs.append(pair)

    results.append({
        "Asset": sym,
        "Alpaca Pair": pair,
        "Tradable": is_tradable,
        "Min Order Size": min_order_size,
        "Data Feed": "OK" if has_bars else "FAIL",
        "Status": status
    })

import pandas as pd
df = pd.DataFrame(results)
print(df.to_string(index=False))
print("=" * 75)
print(f"Supported Production Universe ({len(supported_pairs)} pairs): {supported_pairs}")
print("=" * 75)
