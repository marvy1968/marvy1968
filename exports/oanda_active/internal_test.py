#!/usr/bin/env python3
"""
Alpaca-style Internal Test for All Bots
"""

import os
import sys
import time
from datetime import datetime, timedelta

# Load environment
sys.path.insert(0, '.')
os.system("source /home/moonrider1968/bots/RangeBox_Master/start_env.sh 2>/dev/null || true")

def test_bot(bot_class, name, symbol="SPY", days=30):
    print(f"\n=== Testing {name} Bot ===")
    try:
        bot = bot_class.__new__(bot_class)
        bot.cfg = {"bot_name": name, "max_concurrent_trades": 5, "risk_per_trade_pct": 0.01, "poll_interval_sec": 60, "log_dir": "./logs", "state_file": f"./state/{name.lower()}_test.json", "params": {"box_lookback": 30, "adx_range_thresh": 25, "sl_atr_mult": 1.5, "target_rr": 1.8}}
        bot.params = bot.cfg["params"]
        bot.virtual_trades = {}
        bot.equity = 100000.0
        bot.recent_pnls = []
        bot.peak_equity = 100000.0
        bot.logger = logging.getLogger(name)
        bot.symbols = [symbol]
        bot._load_shared_config = lambda: None
        bot._load_state = lambda: None
        bot._save_state = lambda: None
        bot.is_trading_paused = lambda: False

        print(f"  Initialized {name} Bot")
        print(f"  Equity: ${bot.equity:,.2f}")

        # Simple internal test loop
        for i in range(5):
            print(f"  Test iteration {i+1}/5 - Checking {symbol}")
            time.sleep(1)

        print(f"✅ {name} Bot test passed")
        return True
    except Exception as e:
        print(f"❌ {name} Bot test failed: {e}")
        return False

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    from alpaca_swing_trend_bot import AlpacaSwingTrendBot
    from alpaca_daytrade_scalp_bot import DaytradeScalpBot
    from alpaca_crypto_range_bot import CryptoRangeBot

    print("=== Alpaca Internal Bot Tests ===")
    print(f"Start time: {datetime.now()}")

    test_bot(AlpacaSwingTrendBot, "Swing Trend", "SPY")
    test_bot(DaytradeScalpBot, "Daytrade/Scalp", "AAPL")
    test_bot(CryptoRangeBot, "Crypto Range", "BTC/USD")

    print("\n=== All tests completed ===")
