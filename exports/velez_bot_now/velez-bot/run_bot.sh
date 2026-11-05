#!/bin/bash
cd /home/moonrider1968/velez-bot/velez-bot
source venv/bin/activate

echo "=== Velez Bot Auto-Restart Wrapper Started at $(date) ==="

while true; do
    echo "[$(date)] Starting bot..."
    python velez_modular_bot.py
    echo "[$(date)] Bot stopped or crashed. Restarting in 5 seconds..."
    sleep 5
done
