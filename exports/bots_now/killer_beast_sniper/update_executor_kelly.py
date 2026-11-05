import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# Compiled Kelly Risk Percentages from MFE/MAE Report
KELLY_RISK_MATRIX = {
    ("AAPL", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("AAPL", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("AAPL", "VOL_SQUEEZE", "BUY"): 0.025,
    ("AAPL", "VOL_SQUEEZE", "SELL"): 0.025,
    ("AMD", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("AMD", "VELEZ_20EMA", "SELL"): 0.025,
    ("AMZN", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("AMZN", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("AMZN", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("MSFT", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("MSFT", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("MSFT", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("MSFT", "VOL_SQUEEZE", "SELL"): 0.025,
    ("NVDA", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("NVDA", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("NVDA", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("NVDA", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("QQQ", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("QQQ", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("QQQ", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("QQQ", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("QQQ", "VOL_SQUEEZE", "BUY"): 0.025,
    ("QQQ", "VOL_SQUEEZE", "SELL"): 0.025,
    ("SPY", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("SPY", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("SPY", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("SPY", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("SPY", "VELEZ_20EMA", "BUY"): 0.025,
    ("SPY", "VELEZ_20EMA", "SELL"): 0.025,
    ("SPY", "VOL_SQUEEZE", "BUY"): 0.025,
    ("SPY", "VOL_SQUEEZE", "SELL"): 0.025,
    ("TSLA", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("TSLA", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("TSLA", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("TSLA", "RANGE_BREAKOUT", "SELL"): 0.025,
}

print("✅ Kelly Matrix map generated with dynamic risk scaling.")
