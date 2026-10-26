#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/oanda_master_alliance}"

cd "$BOT_DIR"

exec "$BOT_DIR/.venv/bin/python" \
  "$BOT_DIR/run_segment4_integrity.py"
