#!/bin/bash

cd /home/moonrider1968/bots/killer_beast_sniper

# Activate virtual environment
source venv/bin/activate

# Kill any existing instance
pkill -9 -f fx_titan_decamatrix.py || true

echo "🚀 Starting DECA-MATRIX v2.4 with venv..."

# Start the bot in background
nohup python fx_titan_decamatrix.py > deca_matrix_runtime.log 2>&1 &

echo "✅ Bot started with PID: $!"
echo "📊 Monitor with: tail -f deca_matrix_runtime.log"
echo "📋 Full log:     tail -f deca_matrix.log"
