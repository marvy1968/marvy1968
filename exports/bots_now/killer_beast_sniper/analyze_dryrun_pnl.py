import os
import re
import sys
import requests
from datetime import datetime, timezone

# --- CONFIGURATION ---
LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/forex_daemon.log"
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "REDACTED_OANDA_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")

OANDA_URL = "https://api-fxpractice.oanda.com" if OANDA_ENV == "practice" else "https://api-fxtrade.oanda.com"
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# Regex to match: 2026-08-10 04:24:37,796 [INFO] 🧪 [DRY RUN SIGNAL MATCH: BUY_BREAKOUT] AUD_USD BUY 42,580 Units
LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*?\[DRY RUN SIGNAL MATCH:\s*(\w+)\]\s+(\w+_\w+)\s+(BUY|SELL)\s+([\d,]+)\s+Units"
)

def get_current_pricing(pair: str):
    """Fetch current mid price from OANDA pricing API."""
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={pair}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            prices = resp.json().get('prices', [])
            if prices and prices[0].get('bids') and prices[0].get('asks'):
                bid = float(prices[0]['bids'][0]['price'])
                ask = float(prices[0]['asks'][0]['price'])
                return (bid + ask) / 2.0
    except Exception as e:
        print(f"Error fetching current price for {pair}: {e}")
    return None

def get_historical_entry_price(pair: str, timestamp_str: str):
    """Fetch M1 candle close price closest to the log timestamp."""
    try:
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        iso_time = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        url = f"{OANDA_URL}/v3/instruments/{pair}/candles?from={iso_time}&count=1&granularity=M1&price=M"
        resp = requests.get(url, headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            candles = resp.json().get('candles', [])
            if candles:
                return float(candles[0]['mid']['c'])
    except Exception as e:
        print(f"Error fetching historical price for {pair} at {timestamp_str}: {e}")
    return None

def calculate_pnl(pair: str, side: str, units: int, entry_price: float, current_price: float) -> float:
    """Calculate unrealized PnL in USD based on quote/base currency structure."""
    direction = 1.0 if side.upper() == 'BUY' else -1.0
    price_diff = (current_price - entry_price) * direction
    
    # USD Quote Pair (e.g. EUR_USD, GBP_USD, AUD_USD)
    if pair.endswith("_USD"):
        return price_diff * units
    # USD Base Pair (e.g. USD_JPY, USD_CAD)
    elif pair.startswith("USD_"):
        return (price_diff / current_price) * units
    else:
        # Fallback approximation for cross pairs
        return price_diff * units

def parse_and_analyze():
    if not os.path.exists(LOG_FILE):
        print(f"❌ Log file not found at: {LOG_FILE}")
        return

    signals = []
    with open(LOG_FILE, "r") as f:
        for line in f:
            match = LOG_PATTERN.search(line)
            if match:
                time_str, strategy, pair, side, units_str = match.groups()
                units = int(units_str.replace(",", ""))
                signals.append({
                    "time": time_str,
                    "strategy": strategy,
                    "pair": pair,
                    "side": side,
                    "units": units
                })

    if not signals:
        print("ℹ️ No dry-run signal matches found in log file yet.")
        return

    print("=" * 110)
    print(f"📊 DRY-RUN HYPOTHETICAL P&L PERFORMANCE REPORT ({len(signals)} Signals Found)")
    print("=" * 110)
    print(f"{'Timestamp (UTC)':<20} | {'Strategy':<15} | {'Pair':<8} | {'Side':<4} | {'Units':>9} | {'Entry Price':>10} | {'Current':>10} | {'P&L ($)':>10}")
    print("-" * 110)

    total_pnl = 0.0
    wins = 0
    losses = 0

    # Cache current prices to avoid duplicate API hits
    cached_current_prices = {}

    for s in signals:
        pair = s["pair"]
        if pair not in cached_current_prices:
            cached_current_prices[pair] = get_current_pricing(pair)
            
        curr_price = cached_current_prices[pair]
        entry_price = get_historical_entry_price(pair, s["time"])

        if entry_price and curr_price:
            pnl = calculate_pnl(pair, s["side"], s["units"], entry_price, curr_price)
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
            
            pnl_str = f"${pnl:+9.2f}"
            print(f"{s['time']:<20} | {s['strategy']:<15} | {pair:<8} | {s['side']:<4} | {s['units']:>9,} | {entry_price:>10.5f} | {curr_price:>10.5f} | {pnl_str:>10}")
        else:
            print(f"{s['time']:<20} | {s['strategy']:<15} | {pair:<8} | {s['side']:<4} | {s['units']:>9,} | {'N/A':>10} | {'N/A':>10} | {'N/A':>10}")

    print("-" * 110)
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
    print(f"SUMMARY: Total P&L: ${total_pnl:+,.2f}  |  Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)")
    print("=" * 110)

if __name__ == "__main__":
    parse_and_analyze()
