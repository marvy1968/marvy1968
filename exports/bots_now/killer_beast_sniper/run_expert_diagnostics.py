import sys
import yfinance as yf
import pandas as pd

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from master_expert_orchestrator import evaluate_all_experts

# Watchlist configuration with prepost parameter toggle
TARGETS = [
    ("AAPL", "STOCKS", "1m", "1d", True),
    ("NVDA", "STOCKS", "1m", "1d", True),
    ("TSLA", "STOCKS", "1m", "1d", True),
    ("BTC-USD", "CRYPTO", "1h", "7d", False),
    ("EURUSD=X", "FOREX", "1m", "1d", False)
]

def format_candles(df):
    """Formats yfinance OHLC dataframe into expert dictionary format."""
    candles = []
    for idx, row in df.iterrows():
        candles.append({
            'h': float(row['High']),
            'l': float(row['Low']),
            'c': float(row['Close']),
            'close': float(row['Close']),
            'high': float(row['High']),
            'low': float(row['Low'])
        })
    return candles

def main():
    print("=" * 85)
    print("🤖 REAL-TIME 10-EXPERT QUANTITATIVE SIGNAL DIAGNOSTIC (PRE-MARKET ENABLED)")
    print("=" * 85)

    total_signals_fired = 0

    for ticker, asset_type, interval, period, include_prepost in TARGETS:
        display_ticker = "BTC/USD" if ticker == "BTC-USD" else ("EUR_USD" if ticker == "EURUSD=X" else ticker)
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period=period, interval=interval, prepost=include_prepost)

            if df.empty or len(df) < 30:
                print(f"⚠️ [{display_ticker:<7}] Insufficient market data returned ({len(df)} bars)")
                print("-" * 85)
                continue

            candles = format_candles(df)
            res = evaluate_all_experts(display_ticker, candles, timeframe=interval)

            if not res:
                print(f"⚠️ [{display_ticker:<7}] Evaluation returned null result.")
                print("-" * 85)
                continue

            symbol = res['symbol']
            price = res['price']
            regime = res['regime']
            adx = res['adx']
            chop = res['chop']
            signals = res['signals']

            ext_label = " (Pre-Market)" if include_prepost else ""
            print(f"📌 {symbol:<8}{ext_label:<13} | Asset Class: {asset_type:<6} | Price: ${price:,.2f} | Regime: {regime:<8} (ADX: {adx:.1f}, CHOP: {chop:.1f})")

            if signals:
                for name, side, category in signals:
                    total_signals_fired += 1
                    print(f"   ⚡ SIGNAL FIRED -> [{category:<7}] Expert: {name:<20} | Side: {side}")
            else:
                print("   ℹ️ No expert setups matched current candle conditions.")

            print("-" * 85)

        except Exception as e:
            print(f"❌ [{display_ticker:<7}] Diagnostic exception: {e}")
            print("-" * 85)

    print(f"📊 SUMMARY: {total_signals_fired} total signals active across target watchlist.")
    print("=" * 85)

if __name__ == "__main__":
    main()
