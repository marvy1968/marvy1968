#!/bin/bash
MODE=$1
if [ "$MODE" = "start" ]; then
    echo "Starting Weinstein backtest (2017-2021)..."
    nohup python weinstein.py --start 2017-01-01 --end 2021-12-31 > backtest_raw.log 2>&1 &
    echo "Backtest process initiated in background (PID $!)."
elif [ "$MODE" = "status" ]; then
    if pgrep -f "python weinstein.py" > /dev/null; then
        echo "Status: Running..."
    else
        echo "Status: Finished or stopped."
    fi
elif [ "$MODE" = "results" ]; then
    if [ -f "backtest_raw.log" ]; then
        tail -50 backtest_raw.log
    else
        echo "No backtest log found yet."
    fi
else
    echo "Usage: bash research.sh [start|status|results]"
fi
