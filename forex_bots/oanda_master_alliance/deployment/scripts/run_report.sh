#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/oanda_master_alliance}"

cd "$BOT_DIR"

"$BOT_DIR/.venv/bin/python" \
  "$BOT_DIR/run_segment4_alerts.py"

"$BOT_DIR/.venv/bin/python" \
  "$BOT_DIR/build_segment4a1_report.py"

"$BOT_DIR/.venv/bin/python" \
  "$BOT_DIR/build_segment4a2_report.py"

"$BOT_DIR/.venv/bin/python" \
  "$BOT_DIR/build_segment4a3_report.py"
