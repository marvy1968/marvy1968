import sys
import json
import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# Bounded Min/Max SL and TP Guardrails by Asset Class
BOUNDS = {
    "STOCKS": {"min_sl": 0.0002, "max_sl": 0.0020, "min_tp": 0.0003, "max_tp": 0.0040},
    "CRYPTO": {"min_sl": 0.0100, "max_sl": 0.0350, "min_tp": 0.0200, "max_tp": 0.0800}
}

def calculate_adaptive_params(current_price, recent_atr, asset_class="STOCKS"):
    """
    Dynamically projects optimal SL/TP based on recent N-candle ATR volatility
    """
    atr_pct = recent_atr / current_price
    
    # Target SL = 1.2x ATR% | Target TP = 2.0x ATR%
    raw_sl = round(atr_pct * 1.2, 5)
    raw_tp = round(atr_pct * 2.0, 5)
    
    b = BOUNDS[asset_class]
    clamped_sl = max(min(raw_sl, b["max_sl"]), b["min_sl"])
    clamped_tp = max(min(raw_tp, b["max_tp"]), b["min_tp"])
    
    return clamped_sl, clamped_tp

def main():
    print("=" * 75)
    print("🧠 AUTO-PARAMETER TUNER | Candle Volatility Calibration")
    print("=" * 75)
    
    # Example Calibration Run for Current Volatility Conditions
    sample_tickers = [
        ("AAPL", 308.26, 0.08, "STOCKS"),
        ("NVDA", 217.55, 0.12, "STOCKS"),
        ("BTC/USD", 62500.0, 950.0, "CRYPTO"),
        ("SOL/USD", 145.0, 3.2, "CRYPTO")
    ]
    
    print(f"{'Ticker':<10} | {'Price':>10} | {'ATR':>8} | {'New SL %':>10} | {'New TP %':>10}")
    print("-" * 75)
    
    for ticker, price, atr, class_type in sample_tickers:
        sl, tp = calculate_adaptive_params(price, atr, class_type)
        print(f"{ticker:<10} | ${price:>9.2f} | {atr:>8.2f} | {sl*100:>9.3f}% | {tp*100:>9.3f}%")
        
    print("=" * 75)

if __name__ == "__main__":
    main()
