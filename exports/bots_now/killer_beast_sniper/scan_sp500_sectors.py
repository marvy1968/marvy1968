import os
import sys
import requests
import yfinance as yf
import pandas as pd

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from master_expert_orchestrator import evaluate_all_experts

# Environment & Alpaca Config
APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

SECTOR_WATCHLIST = {
    "Information Technology": ["AAPL", "NVDA", "MSFT", "AVGO", "ORCL", "CRM", "AMD", "ACN", "CSCO", "ADBE"],
    "Financials": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "C", "BLK", "AXP"],
    "Healthcare": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "PFE", "DHR", "ISRG"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"],
    "Communication Services": ["GOOGL", "META", "NFLX", "TMUS", "DIS", "VZ", "T", "CMCSA", "OMC"],
    "Industrials": ["GE", "CAT", "RTX", "HON", "UNP", "DE", "BA", "LMT", "ADP", "UPS"],
    "Consumer Staples": ["PG", "COST", "WMT", "KO", "PEP", "PM", "MDLZ", "CL", "MO", "TGT"],
    "Energy": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "WMB", "OXY"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "GEV", "AEP", "SRE", "D", "PEG", "ED"],
    "Real Estate": ["PLD", "AMT", "EQIX", "WELL", "SPG", "DLR", "O", "CCI", "PSA", "CBRE"],
    "Materials": ["LIN", "APD", "ECL", "SHW", "NEM", "FCX", "DOW", "CTVA", "DD", "PPG"]
}

def format_candles(df):
    candles = []
    for idx, row in df.iterrows():
        candles.append({
            'h': float(row['High']), 'l': float(row['Low']), 'c': float(row['Close']),
            'close': float(row['Close']), 'high': float(row['High']), 'low': float(row['Low'])
        })
    return candles

def get_alpaca_account():
    if not APCA_KEY or not APCA_SECRET:
        return None
    try:
        r = requests.get(f"{BASE_URL}/account", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def submit_alpaca_paper_order(symbol, qty, side):
    url = f"{BASE_URL}/orders"
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "gtc"
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=5)
        return r.json(), r.status_code
    except Exception as e:
        return str(e), 500

def resolve_signal_conflicts(signals, regime):
    """Nets out opposite signals (BUY vs SELL) based on current market regime."""
    sides = set([s[1] for s in signals])
    if "BUY" in sides and "SELL" in sides:
        # If both sides fire, prioritize SWING in TRENDING, SCALPER in RANGING
        preferred_cat = "SWING" if regime == "TRENDING" else "SCALPER"
        filtered = [s for s in signals if s[2] == preferred_cat]
        return filtered if filtered else [signals[0]]
    return signals

def main():
    print("=" * 90)
    print("🌐 S&P 500 MULTI-SECTOR SCANNER (CONFLICT RESOLVED & DYNAMIC MARGIN)")
    print("=" * 90)

    account = get_alpaca_account()
    if account:
        equity = float(account.get("equity", 0))
        buying_power = float(account.get("buying_power", 0))
        print(f"💳 ALPACA PAPER ACCOUNT ACTIVE | Equity: ${equity:,.2f} | Buying Power: ${buying_power:,.2f}")
    else:
        print("ℹ️ Running scanner in simulation mode.")

    print("-" * 90)
    total_signals = 0

    for sector_name, tickers in SECTOR_WATCHLIST.items():
        print(f"\n📂 SECTOR: {sector_name.upper()}")
        print("-" * 90)

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                df = stock.history(period="1d", interval="1m", prepost=True)

                if df.empty or len(df) < 20:
                    continue

                candles = format_candles(df)
                res = evaluate_all_experts(ticker, candles, timeframe="1m")

                if not res or not res.get("signals"):
                    continue

                symbol = res['symbol']
                price = res['price']
                regime = res['regime']
                adx = res['adx']

                # Deduplicate and resolve BUY/SELL conflicts
                clean_signals = resolve_signal_conflicts(res['signals'], regime)

                for name, side, category in clean_signals:
                    total_signals += 1
                    print(f"📌 [{symbol:<5}] Price: ${price:>7.2f} | Regime: {regime:<8} (ADX: {adx:.1f}) | ⚡ [{category}] {name} ({side})")

                    if account:
                        # Refresh available buying power dynamically
                        curr_acc = get_alpaca_account() or account
                        curr_bp = float(curr_acc.get("buying_power", 0))
                        
                        # Dynamic sizing: Cap allocation to min($2500, 2.5% of remaining BP)
                        target_alloc = max(300, min(2500, curr_bp * 0.025))
                        qty = max(1, int(target_alloc / price))

                        resp, status = submit_alpaca_paper_order(symbol, qty, side)
                        if status in [200, 201]:
                            print(f"   ✅ [ALPACA ORDER FILLED] {side} {qty} shares of {symbol}")
                        else:
                            print(f"   ❌ [ALPACA ORDER REJECTED] HTTP {status}: {resp}")

            except Exception:
                continue

    print("=" * 90)
    print(f"📊 MULTI-SECTOR SCAN COMPLETE: {total_signals} reconciled signals processed.")
    print("=" * 90)

if __name__ == "__main__":
    main()
