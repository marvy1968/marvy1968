#!/bin/bash

BASE_DIR="/home/moonrider1968/bots/crypto_fleet"
PYTHON_BIN="/home/moonrider1968/bots/alpaca_microedge/venv/bin/python3"

start_fleet() {
    echo "🚀 Starting Crypto Fleet Daemons..."
    nohup $PYTHON_BIN $BASE_DIR/crypto_master_brain.py > /dev/null 2>&1 &
    nohup $PYTHON_BIN $BASE_DIR/crypto_stage4_short.py > /dev/null 2>&1 &
    nohup $PYTHON_BIN $BASE_DIR/crypto_grid_arb.py > /dev/null 2>&1 &
    echo "✅ Fleet successfully launched in background."
}

stop_fleet() {
    echo "🛑 Stopping all Crypto Fleet Daemons..."
    pkill -f crypto_master_brain.py
    pkill -f crypto_stage4_short.py
    pkill -f crypto_grid_arb.py
    echo "✅ All processes stopped."
}

status_fleet() {
    echo "🔍 Active Crypto Fleet Processes:"
    ps aux | grep -E "crypto_master_brain|crypto_stage4|crypto_grid" | grep -v grep
}

case "$1" in
    start)
        start_fleet
        ;;
    stop)
        stop_fleet
        ;;
    restart)
        stop_fleet
        sleep 2
        start_fleet
        ;;
    status)
        status_fleet
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
esac
