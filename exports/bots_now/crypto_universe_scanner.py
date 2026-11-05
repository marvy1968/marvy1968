#!/usr/bin/env python3
"""
Crypto Liquidity Universe Engine (Kraken Spot Normalized)
Aggregates volume across major quote currencies (USD, USDT, USDC, EUR),
selects the primary USD pair per base asset, and outputs the Top 30 tradeable universe.
"""

import time
import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import ccxt

EXCHANGE_ID = "kraken"
QUOTE_CURRENCIES = {"USD", "USDT", "USDC", "EUR"}
PREFERRED_QUOTES = ["USD", "USDT", "USDC", "EUR"]
MIN_LISTING_DAYS = 60
MIN_MEDIAN_DAILY_TURNOVER = 500_000.0  # $500k/day median turnover across Kraken
MAX_BID_ASK_SPREAD_PCT = 0.0050        # 0.50% max spread on primary pair
TARGET_UNIVERSE_SIZE = 30

EXCLUDED_PATTERNS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "USDD", "PYUSD", 
    "EUR", "GBP", "CAD", "JPY", "AUD", "CHF", "USD",
    "WBTC", "WBETH", "WETH", "WBNB", "WSOL", "TBTC",
    "STETH", "CBETH", "RETH", "METH", "BETH", "EETH", "EZETH", "SUSD", "SETH"
}

def is_excluded_symbol(base: str) -> bool:
    base_clean = base.upper().replace("-", "").replace("_", "").replace(".", "")
    if base_clean in EXCLUDED_PATTERNS:
        return True
    for suffix in ("UP", "DOWN", "BEAR", "BULL", "STETH", "ETH", "BTC"):
        if base_clean.endswith(suffix) and len(base_clean) > len(suffix) + 1:
            if suffix in ("UP", "DOWN", "BEAR", "BULL"):
                return True
    return False

def main():
    print("=" * 95)
    print(f" 🌐 CRYPTO LIQUIDITY UNIVERSE BUILDER | VENUE: {EXCHANGE_ID.upper()}")
    print("=" * 95)

    exchange_class = getattr(ccxt, EXCHANGE_ID)
    exchange = exchange_class({"enableRateLimit": True, "timeout": 30000})

    print("Fetching Kraken markets and 24h ticker snapshots...")
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    # Step 1: Group symbols by base currency
    base_to_pairs = {}
    for symbol, market in markets.items():
        if not market.get("active", True) or not market.get("spot", True):
            continue
        base = market.get("base")
        quote = market.get("quote")
        if not base or not quote or quote not in QUOTE_CURRENCIES:
            continue
        if is_excluded_symbol(base):
            continue

        base_to_pairs.setdefault(base, []).append((symbol, quote))

    print(f"Discovered {len(base_to_pairs)} candidate base assets across valid quote pairs.")
    print("Scanning volume and spreads...")

    candidate_results = []
    lookback_days = 90

    for idx, (base, pairs) in enumerate(base_to_pairs.items(), 1):
        try:
            # Pick primary pair by PREFERRED_QUOTES priority
            primary_symbol = None
            for pref in PREFERRED_QUOTES:
                match = [s for s, q in pairs if q == pref]
                if match:
                    primary_symbol = match[0]
                    break
            if not primary_symbol:
                primary_symbol = pairs[0][0]

            ticker = tickers.get(primary_symbol)
            if not ticker or not ticker.get("bid") or not ticker.get("ask") or ticker["bid"] <= 0:
                continue

            spread_pct = (ticker["ask"] - ticker["bid"]) / ticker["ask"]
            if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                continue

            # Fetch OHLCV on primary trading pair
            ohlcv = exchange.fetch_ohlcv(primary_symbol, timeframe="1d", limit=lookback_days)
            if not ohlcv or len(ohlcv) < MIN_LISTING_DAYS:
                continue

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["quote_volume"] = df["close"] * df["volume"]

            recent_30d = df.tail(30)
            median_turnover_30d = recent_30d["quote_volume"].median()

            if median_turnover_30d < MIN_MEDIAN_DAILY_TURNOVER:
                continue

            last_px = ticker.get("last", ticker.get("close", 0.0))

            candidate_results.append({
                "symbol": primary_symbol,
                "base": base,
                "median_daily_turnover_30d": median_turnover_30d,
                "spread_pct": spread_pct * 100.0,
                "price": last_px,
                "history_bars": len(df)
            })

            time.sleep(0.04)

        except Exception:
            continue

    df_results = pd.DataFrame(candidate_results)
    if df_results.empty:
        print("❌ No tokens met the liquidity criteria.")
        return

    df_ranked = df_results.sort_values(by="median_daily_turnover_30d", ascending=False).reset_index(drop=True)
    top_universe = df_ranked.head(TARGET_UNIVERSE_SIZE)

    print("\n" + "=" * 95)
    print(f" 🏆 TOP {len(top_universe)} POINT-IN-TIME QUALIFIED CRYPTO UNIVERSE (KRAKEN)")
    print("=" * 95)
    header = f"{'Rank':<5} | {'Symbol':<12} | {'Base':<8} | {'30D Med Turnover ($M)':<22} | {'Spread (%)':<10} | {'Price ($)':<12}"
    print(header)
    print("-" * 95)
    for rank, row in top_universe.iterrows():
        turnover_m = row["median_daily_turnover_30d"] / 1_000_000.0
        print(f"{rank + 1:<5} | {row['symbol']:<12} | {row['base']:<8} | ${turnover_m:>19.2f}M | {row['spread_pct']:>9.3f}% | ${row['price']:>10.4f}")
    print("=" * 95)

    symbols_list = top_universe["symbol"].tolist()
    print(f"\nFinal Scannable Universe Array (N={len(symbols_list)}):")
    print(symbols_list)

if __name__ == "__main__":
    main()
