#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
mkdir -p logs reports state data
python -m py_compile roi_bot.py
echo "Installed. Edit .env and config/config.yaml, then run:"
echo "  .venv/bin/python roi_bot.py scan"
echo "  .venv/bin/python roi_bot.py backtest"
echo "Paper execution requires execution.dry_run: false"
