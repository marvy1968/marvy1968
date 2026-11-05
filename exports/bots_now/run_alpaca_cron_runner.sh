#!/usr/bin/env bash
set -e

WORKDIR="/home/moonrider1968/bots"
ENV_FILE="$WORKDIR/.env"

# Explicitly load environment variables from .env
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

LOGDIR="$WORKDIR/stock_data/logs"
mkdir -p "$LOGDIR"

DATE_STR=$(date -u +"%Y%m%d")
LOG_FILE="$LOGDIR/cron_alpaca_dual_sleeve_${DATE_STR}.log"

echo "========================================================" >> "$LOG_FILE"
echo "Starting Alpaca Dual-Sleeve Run at $(date -u)" >> "$LOG_FILE"
echo "========================================================" >> "$LOG_FILE"

cd "$WORKDIR"
/usr/bin/python3 "$WORKDIR/alpaca_dual_sleeve_daemon.py" >> "$LOG_FILE" 2>&1
echo "Finished Alpaca Dual-Sleeve Run at $(date -u)" >> "$LOG_FILE"
