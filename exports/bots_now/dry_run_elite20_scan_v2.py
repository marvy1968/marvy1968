#!/usr/bin/env python3
"""
Elite 20 Equities: Multi-Symbol Batch Scan Dry Run (v2)
- Uses Alpaca Data v2 Multi-Symbol Bars endpoint: /v2/stocks/bars
- Auto-detects available data feed (SIP vs IEX)
- Vectorized 50 SMA, -2.5-Sigma, -3.5-Sigma calculations
- Position sizing against live $53,947.15 NAV
"""

import os
import sys
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
DATA_URL = "https://data.alpaca.markets/v2"

SESSION = requests.Session()
SESSION.headers.update({
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
})

NY_TZ = ZoneInfo("America/New_York")

SYMBOL_TO_SECTOR = {
    "SPY": "INDEX", "QQQ": "INDEX",
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH",
    "GOOGL": "COMM", "META": "COMM", "NFLX": "COMM",
    "WMT": "STAPLES", "COST": "STAPLES", "PG": "STAPLES",
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY",
    "AMZN": "DISC",
    "CAT": "IND", "GE": "IND",
    "JPM": "FIN", "GS": "FIN"
}

ELITE_20_SYMBOLS = sorted(list(SYMBOL_TO_SECTOR.keys()))
BASE_RISK_PCT = 0.0050      # 0.50% NAV risk per trade
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Open Portfolio Risk
MAX_CONCURRENT_POSITIONS = 3
MAX_PER_SECTOR = 1

def fetch_batch_daily_bars(symbols: list, limit: int = 100) -> dict:
    symbols_str = ",".join(symbols)
    endpoint = f"{DATA_URL}/stocks/bars"
    
    # Try SIP feed first; fallback to IEX if SIP unauthorized on paper tier
    for feed in ["sip", "iex"]:
        params = {
            "symbols": symbols_str,
            "timeframe": "1Day",
            "limit": limit,
            "adjustment": "all",
            "feed": feed
        }
        resp = SESSION.get(endpoint, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if "bars" in data and data["bars"]:
                print(f"✅ Market Data Feed Connected: {feed.upper()} consolidated feed.")
                result = {}
                for sym, bars in data["bars"].items():
                    records = []
                    for b in bars:
                        records.append({
                            "timestamp": pd.to_datetime(b["t"]).astimezone(NY_TZ),
                            "open": float(b["o"]),
                            "high": float(b["h"]),
                            "low": float(b["l"]),
                            "close": float(b["c"]),
                            "volume": int(b["v"])
                        })
                    df = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
                    result[sym] = df
                return result
        else:
            print(f"⚠️ Feed {feed.upper()} returned {resp.status_code}: {resp.text[:100]}")
    return {}

def main():
    print("=" * 115)
    print(" 🔍 ELITE 20 EQUITIES: BATCH POST-CLOSE SCAN DRY RUN (v2)")
    print(f" Environment: {ALPACA_ENVIRONMENT.upper()} | Base URL: {BASE_URL}")
    print("=" * 115)

    account_resp = SESSION.get(f"{BASE_URL}/account", timeout=10)
    pos_resp = SESSION.get(f"{BASE_URL}/positions", timeout=10)
    
    if account_resp.status_code != 200:
        print(f"❌ Failed to connect to Alpaca Account API: {account_resp.text}")
        return

    account = account_resp.json()
    pos_list = pos_resp.json() if pos_resp.status_code == 200 and isinstance(pos_resp.json(), list) else []

    nav = float(account["equity"])
    cash = float(account["cash"])

    print(f"\n 💼 Live Account State:")
    print(f"    NAV:                 ${nav:,.2f}")
    print(f"    Cash Balance:        ${cash:,.2f}")
    print(f"    Active Positions:    {len(pos_list)} / {MAX_CONCURRENT_POSITIONS}")
    if pos_list:
        for p in pos_list:
            print(f"      - {p['symbol']} ({SYMBOL_TO_SECTOR.get(p['symbol'], 'OTHER')}): {p['qty']} shares @ ${float(p['avg_entry_price']):.2f} (Current: ${float(p['current_price']):.2f})")
    print("-" * 115)

    active_symbols = set(p["symbol"] for p in pos_list)
    active_sectors = set(SYMBOL_TO_SECTOR.get(s, "UNKNOWN") for s in active_symbols)

    bars_by_sym = fetch_batch_daily_bars(ELITE_20_SYMBOLS, limit=100)

    scan_results = []
    setups = []

    print(f"\n 📈 Analyzing Indicators & Reclaim States for {len(ELITE_20_SYMBOLS)} Constituents...\n")
    for sym in ELITE_20_SYMBOLS:
        sec = SYMBOL_TO_SECTOR[sym]
        df = bars_by_sym.get(sym)

        if df is None or len(df) < 55:
            scan_results.append({
                "Symbol": sym, "Sector": sec, "Status": "NO_DATA",
                "Last Bar Date": "N/A", "Last Close": "N/A", "50 SMA": "N/A",
                "-2.5σ (Entry)": "N/A", "-3.5σ (Stop)": "N/A", "Setup Signal": "NO"
            })
            continue

        df["ma50"] = df["close"].rolling(50).mean()
        df["std50"] = df["close"].rolling(50).std()
        df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
        df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        c_px = float(cur["close"])
        ma50 = float(cur["ma50"])
        l25 = float(cur["lower_25s"])
        l35 = float(cur["lower_35s"])
        bar_date = cur["timestamp"].strftime("%Y-%m-%d")

        # Reclaim condition: Prior close < -2.5 sigma, Current close > -2.5 sigma and < 50 SMA
        is_reclaim = (prev["close"] < prev["lower_25s"]) and (cur["close"] > cur["lower_25s"]) and (cur["close"] < cur["ma50"])

        status = "ELIGIBLE"
        if sym in active_symbols:
            status = "ACTIVE_POS"
        elif sec in active_sectors:
            status = "SECTOR_BLOCKED"

        signal_str = "RECLAIM_TRIGGER" if is_reclaim else "NO"

        scan_results.append({
            "Symbol": sym,
            "Sector": sec,
            "Status": status,
            "Last Bar Date": bar_date,
            "Last Close": f"${c_px:.2f}",
            "50 SMA": f"${ma50:.2f}",
            "-2.5σ (Entry)": f"${l25:.2f}",
            "-3.5σ (Stop)": f"${l35:.2f}",
            "Setup Signal": signal_str
        })

        if is_reclaim:
            stop_dist = c_px - l35
            target_risk_usd = nav * BASE_RISK_PCT
            shares = int(math.floor(target_risk_usd / stop_dist)) if stop_dist > 0 else 0
            setups.append({
                "symbol": sym, "sector": sec, "status": status, "date": bar_date,
                "close": c_px, "stop": l35, "target": ma50,
                "stop_dist": stop_dist, "shares": shares, "risk_usd": target_risk_usd
            })

    print(pd.DataFrame(scan_results).to_string(index=False))
    print("=" * 115)

    print("\n 🎯 RECLAIM SETUPS IDENTIFIED:")
    if not setups:
        print("    No active -2.5σ reclaim setups triggered on the latest finalized bar.")
    else:
        for s in setups:
            print(f"    • {s['symbol']} ({s['sector']}) [{s['date']}] -> Status: {s['status']}")
            print(f"      Close: ${s['close']:.2f} | Target (50 SMA): ${s['target']:.2f} | Stop (-3.5σ): ${s['stop']:.2f} (Risk Dist: ${s['stop_dist']:.2f})")
            print(f"      Calculated Sizing: {s['shares']} shares (${s['risk_usd']:,.2f} risk / 0.50% NAV)")
            if s['status'] == 'SECTOR_BLOCKED':
                print(f"      ⚠️ Sizing aborted by Sector Firewall: {s['sector']} already occupied.")
            elif s['status'] == 'ACTIVE_POS':
                print(f"      ⚠️ Sizing aborted: Position in {s['symbol']} already active.")
    print("=" * 115)

if __name__ == "__main__":
    main()
