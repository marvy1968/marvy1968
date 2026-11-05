import sys
import os
from alpaca_bracket_executor import send_bracket_order

TARGET_SECTOR_BASKET = [
    "NVDA", "AAPL", "MSFT", "AMD", "AVGO",
    "JPM", "V", "GS", "AMZN", "TSLA",
    "HD", "META", "GOOGL", "LLY", "XOM"
]

def run_sector_bracket_scan():
    print("=" * 70)
    print("🎯 RUNNING 11-SECTOR HIGH-BETA OCO BRACKET SCAN")
    print("=" * 70)
    for symbol in TARGET_SECTOR_BASKET:
        send_bracket_order(symbol=symbol, side="buy", qty=5)
    print("=" * 70)
    print("✅ Sector Bracket Sweep Completed.")

if __name__ == "__main__":
    run_sector_bracket_scan()
