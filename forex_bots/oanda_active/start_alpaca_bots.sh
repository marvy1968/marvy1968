#!/bin/bash
# Simple launcher for the three Alpaca bots

echo "=== Starting Alpaca Trading Bots ==="

source ../RangeBox_Master/start_env.sh 2>/dev/null || true

# Start each bot in background with logging
python3 alpaca_swing_trend_bot.py > logs/swing.log 2>&1 &
echo "Started Swing Trend Bot (PID $!)"

python3 alpaca_daytrade_scalp_bot.py > logs/daytrade.log 2>&1 &
echo "Started Daytrade/Scalp Bot (PID $!)"

python3 alpaca_crypto_range_bot.py > logs/crypto.log 2>&1 &
echo "Started Crypto Range Bot (PID $!)"

echo ""
echo "Bots are running. Use 'tail -f logs/*.log' to monitor."
echo "To stop all: pkill -f 'alpaca_.*_bot.py'"
