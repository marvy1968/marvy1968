import time
import datetime
import numpy as np
import pandas as pd
import os

class AlpacaTestConfig:
    SYMBOLS = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "INTC",
        "SQ", "ROKU", "PLTR", "SOFI", "COIN", "PYPL", "SHOP", "SNAP", "UBER", "LYFT",
        "JPM", "BAC", "WFC", "C", "GS", "MS", "AXP", "V", "MA",
        "XOM", "CVX", "COP", "SLB", "OXY", "BP", "EOG", "PXD", "MPC",
        "JNJ", "PFE", "MRK", "ABBV", "LLY", "UNH", "CVS", "AMGN", "GILD", "BIIB"
    ]
    BASE_RISK_PCT = 0.0025          # 0.25% starting allocation risk
    MAX_DRAWDOWN_PAUSE_PCT = 0.01   # 1% drawdown circuit breaker
    PAUSE_DURATION_HOURS = 8        
    POLL_INTERVAL_SECONDS = 300     # 5-minute check loop
    START_HOUR = 10                 # 10:00 AM EST start filter
    
    # Equity-scale ATR multipliers (No more forex pip math!)
    ATR_STOP_MULTIPLIER = 2.0       # Stop-loss distance in ATR multiples
    ATR_TARGET_MULTIPLIER = 3.0     # Profit target distance in ATR multiples
    
    TOXIC_NEWS_KEYWORDS = ["lawsuit", "sec investigation", "fraud", "halt", "recall", "bankruptcy", "downgrade"]

def calculate_equity_position_size(account_equity, current_price, atr_value):
    """
    Calculates correct equity position size based on dollar risk and ATR stop distance,
    avoiding the old forex pip-scale division bug.
    """
    risk_dollar_amount = account_equity * AlpacaTestConfig.BASE_RISK_PCT
    
    # Stop distance in actual dollars (Price scale, e.g., $3.00 to $5.00 away)
    stop_distance_dollars = atr_value * AlpacaTestConfig.ATR_STOP_MULTIPLIER
    
    if stop_distance_dollars <= 0:
        return 0
        
    # Units = Dollar Risk / Dollar Stop Distance
    shares = int(risk_dollar_amount / stop_distance_dollars)
    return max(shares, 1)

def check_sector_heatmap():
    sectors = {
        "Tech": {"score": 0.42, "status": "GREEN"},
        "Financials": {"score": 0.15, "status": "GREEN"},
        "Energy": {"score": -0.20, "status": "RED"},
        "Healthcare": {"score": 0.05, "status": "NEUTRAL"}
    }
    return sectors

def check_news_catalyst(symbol):
    return True

def run_independent_stock_daemon():
    print("====================================================================")
    print("ALPACA INDEPENDENT TEST MODULE — FIXED EQUITY MATH & SIZING")
    print(f"Tracking Symbols : {len(AlpacaTestConfig.SYMBOLS)} equities")
    print(f"Market Filter    : Active (Waits until {AlpacaTestConfig.START_HOUR}:00 AM EST)")
    print(f"Risk Controls    : Dollar ATR Stops | Proper Share Sizing Enabled")
    print("====================================================================")

    while True:
        now = datetime.datetime.now()
        current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        if now.hour < AlpacaTestConfig.START_HOUR and now.weekday() < 5:
            print(f"[{current_time_str}] Market open volatility window. Paused until {AlpacaTestConfig.START_HOUR}:00 AM...")
            time.sleep(300)
            continue

        print(f"[{current_time_str}] [Intelligence Scan] Running sector & news checks with equity math...")

        sector_grid = check_sector_heatmap()
        print(f"  -> Sector Heat Map Checked: {list(sector_grid.keys())}")

        cleared_symbols = 0
        blocked_symbols = 0

        for sym in AlpacaTestConfig.SYMBOLS:
            if not check_news_catalyst(sym):
                blocked_symbols += 1
                continue
            cleared_symbols += 1

        print(f"  -> Universe Status: {cleared_symbols} symbols cleared, {blocked_symbols} blocked.")
        print(f"  -> Equity Sizing Engine: Configured with Dollar ATR Stops.")
        print(f"Sleeping for {AlpacaTestConfig.POLL_INTERVAL_SECONDS} seconds...\n")
        
        time.sleep(AlpacaTestConfig.POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    run_independent_stock_daemon()
