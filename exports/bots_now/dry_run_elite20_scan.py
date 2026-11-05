#!/usr/bin/env python3
"""
Elite 20 Equities: Single-Pass Post-Close Scan Dry Run
- Tests Alpaca API connectivity and authentication.
- Verifies SIP feed data extraction for all 20 tickers.
- Calculates 50 SMA, -2.5-Sigma Reclaim, and -3.5-Sigma hard stops.
- Simulates position sizing and sector firewall without submitting live orders.
"""

import os
import sys
import math
from datetime import datetime
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

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    print("❌ ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in environment.")
    sys.exit(1)

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
WARMUP_BARS = 100

def alpaca_request(method: str, endpoint: str, is_data: bool = False, params: dict = None) -> dict:
    url = f"{DATA_URL if is_data else BASE_URL}{endpoint}"
    resp = SESSION.request(method, url, params=params, timeout=15)
    if resp.status_code in [200, 201]:
        return resp.json()
    else:
        print(f"❌ API Error {resp.status_code} on {endpoint}: {resp.text}")
        return None

def fetch_daily_bars(symbol: str) -> pd.DataFrame:
    params = {"timeframe": "1Day", "limit": WARMUP_BARS, "adjustment": "all", "feed": "sip"}
    data = alpaca_request("GET", f"/stocks/{symbol}/bars", is_data=True, params=params)
    if not data or "bars" not in data or not data["bars"]:
        # Fallback to IEX if SIP feed subscription isn't active on paper account
        params["feed"] = "iex"
        data = alpaca_request("GET", f"/stocks/{symbol}/bars", is_data=True, params=params)
        if not data or "bars" not in data or not data["bars"]:
            return None

    records = []
    for b in data["bars"]:
        records.append({
            "timestamp": pd.to_datetime(b["t"]).astimezone(NY_TZ),
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": int(b["v"])
        })
    df = pd.DataFrame(records)
    return df.sort_values("timestamp").reset_index(drop=True)

def main():
    print("=" * 115)
    print(" 🔍 ELITE 20 EQUITIES: ONE-SHOT POST-CLOSE SCAN DRY RUN")
    print(f" Environment: {ALPACA_ENVIRONMENT.upper()} | Base URL: {BASE_URL}")
    print("=" * 115)

    account = alpaca_request("GET", "/account")
    positions = alpaca_request("GET", "/positions")
    
    if not account:
        print("❌ Failed to connect to Alpaca Account API.")
        return

    nav = float(account["equity"])
    cash = float(account["cash"])
    pos_list = positions if isinstance(positions, list) else []

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

    scan_results = []
    setups = []

    print(f"\n 📈 Scanning {len(ELITE_20_SYMBOLS)} Elite 20 Constituents...")
    for sym in ELITE_20_SYMBOLS:
        sec = SYMBOL_TO_SECTOR[sym]
        df = fetch_daily_bars(sym)
        if df is None or len(df) < 55:
            scan_results.append({
                "Symbol": sym, "Sector": sec, "Status": "DATA_ERROR",
                "Last Close": "N/A", "50 SMA": "N/A", "-2.5σ": "N/A", "-3.5σ": "N/A", "Signal": "NO"
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

        # Reclaim condition: Prior close < -2.5 sigma, Current close > -2.5 sigma and < 50 SMA
        is_reclaim = (prev["close"] < prev["lower_25s"]) and (cur["close"] > cur["lower_25s"]) and (cur["close"] < cur["ma50"])

        status = "OPEN"
        if sym in active_symbols:
            status = "ACTIVE_POS"
        elif sec in active_sectors:
            status = "SECTOR_BLOCKED"

        signal_str = "RECLAIM_TRIGGER" if is_reclaim else "NO"

        scan_results.append({
            "Symbol": sym,
            "Sector": sec,
            "Status": status,
            "Last Close": f"${c_px:.2f}",
            "50 SMA": f"${ma50:.2f}",
            "-2.5σ": f"${l25:.2f}",
            "-3.5σ (Stop)": f"${l35:.2f}",
            "Signal": signal_str
        })

        if is_reclaim:
            stop_dist = c_px - l35
            target_risk_usd = nav * BASE_RISK_PCT
            shares = int(math.floor(target_risk_usd / stop_dist)) if stop_dist > 0 else 0
            setups.append({
                "symbol": sym, "sector": sec, "status": status,
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
            print(f"    • {s['symbol']} ({s['sector']}) -> Status: {s['status']}")
            print(f"      Close: ${s['close']:.2f} | Target (50 SMA): ${s['target']:.2f} | Stop (-3.5σ): ${s['stop']:.2f} (Dist: ${s['stop_dist']:.2f})")
            print(f"      Calculated Sizing: {s['shares']} shares (${s['risk_usd']:,.2f} risk / 0.50% NAV)")
            if s['status'] == 'SECTOR_BLOCKED':
                print(f"      ⚠️ Sizing aborted by Sector Firewall: {s['sector']} already occupied.")
            elif s['status'] == 'ACTIVE_POS':
                print(f"      ⚠️ Sizing aborted: Position in {s['symbol']} already active.")
    print("=" * 115)

if __name__ == "__main__":
    main()
