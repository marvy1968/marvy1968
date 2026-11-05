#!/usr/bin/env python3
"""
Inspect extended liquidity ranking on Kraken (Ranks 20-45)
to identify verified non-pegged replacements and audit history bar count.
"""

import time
import pandas as pd
import ccxt

EXCHANGE_ID = "kraken"
QUOTE_CURRENCIES = {"USD", "USDT", "USDC", "EUR"}
PREFERRED_QUOTES = ["USD", "USDT", "USDC", "EUR"]

EXCLUDED_PATTERNS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "USDD", "PYUSD", 
    "EUR", "GBP", "CAD", "JPY", "AUD", "CHF", "USD", "PAXG", "XAUT",
    "WBTC", "WBETH", "WETH", "WBNB", "WSOL", "TBTC",
    "STETH", "CBETH", "RETH", "METH", "BETH", "EETH", "EZETH", "SUSD", "SETH", "USDG"
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
    exchange = ccxt.kraken({"enableRateLimit": True, "timeout": 30000})
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

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

    results = []
    for base, pairs in base_to_pairs.items():
        try:
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
            ohlcv = exchange.fetch_ohlcv(primary_symbol, timeframe="1d", limit=720)
            if not ohlcv or len(ohlcv) < 60:
                continue

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["quote_volume"] = df["close"] * df["volume"]
            median_turnover_30d = df.tail(30)["quote_volume"].median()

            results.append({
                "symbol": primary_symbol,
                "base": base,
                "median_turnover_30d": median_turnover_30d,
                "spread_pct": spread_pct * 100.0,
                "price": ticker.get("last", ticker.get("close", 0.0)),
                "total_bars": len(df)
            })
            time.sleep(0.03)
        except Exception:
            continue

    df_ranked = pd.DataFrame(results).sort_values(by="median_turnover_30d", ascending=False).reset_index(drop=True)

    print("=" * 95)
    print(" 🔍 EXTENDED QUALIFIED UNIVERSE (RANKS 20 TO 45) - AUDIT VIEW")
    print("=" * 95)
    header = f"{'Rank':<5} | {'Symbol':<12} | {'Base':<8} | {'30D Med Turnover ($M)':<22} | {'Spread (%)':<10} | {'Bars Available':<14}"
    print(header)
    print("-" * 95)
    for rank in range(19, min(45, len(df_ranked))):
        row = df_ranked.iloc[rank]
        turnover_m = row["median_turnover_30d"] / 1_000_000.0
        print(f"{rank + 1:<5} | {row['symbol']:<12} | {row['base']:<8} | ${turnover_m:>19.2f}M | {row['spread_pct']:>9.3f}% | {row['total_bars']:>14}")
    print("=" * 95)

if __name__ == "__main__":
    main()
