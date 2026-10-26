#!/usr/bin/env bash
cd /home/moonrider1968/bots/oanda_fifo_practice/MarchEdge || exit 1

while true; do
  echo "=== AUTO LOOP $(date -u) ==="
  python3 auto_settle_and_learn.py
  sleep 300
done
