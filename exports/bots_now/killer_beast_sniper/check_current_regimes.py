import sys
import yfinance as yf
import pandas as pd

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from market_regime import detect_market_regime

# Target Tickers & Asset Classes
TARGETS = [
    ("AAPL", "STOCKS", "1m", "1d"),
    ("NVDA", "STOCKS", "1m", "1d"),
    ("TSLA", "STOCKS", "1m", "1d"),
    ("SPY",  "STOCKS", "1m", "1d"),
    ("BTC-USD", "CRYPTO", "1h", "7d")  # 1h interval for crypto regime evaluation
]

def format_candles(df):
    """Converts yfinance DataFrame into candle dictionary format required by market_regime."""
    candles = []
    for idx, row in df.iterrows():
        candles.append({
            'h': float(row['High']),
            'l': float(row['Low']),
            'c': float(row['Close'])
        })
    return candles

def main():
    print("=" * 80)
    print("📊 REAL-TIME MARKET REGIME & VOLATILITY DIAGNOSTIC")
    print("=" * 80)
    print(f"{'Ticker':<10} | {'Regime':<10} | {'ADX (Trend)':>12} | {'CHOP (Range)':>12} | {'Status':<15}")
    print("-" * 80)

    for ticker, asset_class, interval, period in TARGETS:
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period=period, interval=interval)
            
            if df.empty or len(df) < 30:
                print(f"{ticker:<10} | {'UNKNOWN':<10} | {'N/A':>12} | {'N/A':>12} | ⚠️ Insufficient Data")
                continue

            candles = format_candles(df)
            regime, adx, chop = detect_market_regime(candles)

            # Signal Status Indicator
            if regime == "TRENDING":
                status = "✅ Breakouts Allowed"
            elif regime == "RANGING":
                status = "⛔ Breakouts Blocked"
            else:
                status = "🟡 Neutral State"

            display_ticker = "BTC/USD" if ticker == "BTC-USD" else ticker
            print(f"{display_ticker:<10} | {regime:<10} | {adx:>12.2f} | {chop:>12.2f} | {status:<15}")

        except Exception as e:
            display_ticker = "BTC/USD" if ticker == "BTC-USD" else ticker
            print(f"{display_ticker:<10} | {'ERROR':<10} | {'N/A':>12} | {'N/A':>12} | ⚠️ {e}")

    print("=" * 80)

if __name__ == "__main__":
    main()
