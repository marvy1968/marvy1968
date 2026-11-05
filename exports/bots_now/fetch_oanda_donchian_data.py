import os
import requests
import pandas as pd
import numpy as np
from pathlib import Path

ENV_PATH = Path("/home/moonrider1968/bots/.env")
OUT_DIR = Path("/home/moonrider1968/bots/data/oanda")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. Load .env if present
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

OANDA_API_KEY = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_ACCESS_TOKEN")
OANDA_BASE_URL = os.getenv("OANDA_BASE_URL", "https://api-fxpractice.oanda.com")

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

INSTRUMENTS = ["SPX500_USD", "EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD"]

def fetch_candles(instrument, count=1500, granularity="D"):
    url = f"{OANDA_BASE_URL}/v3/instruments/{instrument}/candles"
    params = {
        "count": count,
        "granularity": granularity,
        "price": "M"
    }
    res = requests.get(url, headers=HEADERS, params=params, timeout=15)
    res.raise_for_status()
    data = res.json().get("candles", [])

    rows = []
    for c in data:
        if not c.get("complete"):
            continue
        mid = c["mid"]
        rows.append({
            "date": pd.to_datetime(c["time"]),
            "open": float(mid["o"]),
            "high": float(mid["h"]),
            "low": float(mid["l"]),
            "close": float(mid["c"]),
            "volume": int(c["volume"])
        })
    
    if not rows:
        print(f"⚠️ No complete rows for {instrument}")
        return

    df = pd.DataFrame(rows).set_index("date").sort_index()

    # Precompute Point-in-Time 55D/20D Channels
    df["high_55"] = df["high"].shift(1).rolling(55).max()
    df["low_55"] = df["low"].shift(1).rolling(55).min()
    df["high_20"] = df["high"].shift(1).rolling(20).max()
    df["low_20"] = df["low"].shift(1).rolling(20).min()

    # ATR14
    prev_c = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_c).abs(),
        (df["low"] - prev_c).abs()
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    out_file = OUT_DIR / f"{instrument}.csv"
    df.to_csv(out_file)
    print(f"✅ Saved {instrument}: {len(df)} daily candles ({df.index[0].date()} to {df.index[-1].date()})")

if __name__ == "__main__":
    for inst in INSTRUMENTS:
        try:
            fetch_candles(inst)
        except Exception as e:
            print(f"❌ Failed to fetch {inst}: {e}")
