#!/bin/bash

# 1. Get the absolute path of the bot directory
DIR="/home/moonrider1968/bots/oanda_fifo_practice"

# 2. Automatically activate the virtual environment
source $DIR/venv/bin/activate

# 3. Kill any existing MicroBeast processes
echo "Stopping any existing MicroBeast instances..."
pkill -f main.py

sleep 2

# 4. Launch the bot
echo "Launching MicroBeast V2 (Kelly + Cooling Logic)..."
nohup python3 $DIR/main.py > $DIR/microbeast.log 2>&1 &

PID=$!
echo "Bot started successfully with PID: $PID"
echo "------------------------------------------------"
echo "To watch the live market hunt, run: tail -f microbeast.log"
