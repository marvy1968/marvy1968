# Alpaca Crypto Strategy Laboratory v1.0
Standalone research companion. It never submits orders.

## Build/update local history
cd ~/bots/alpaca_crypto_roi_bot/strategy_lab
../.venv/bin/python -u download_history.py --years 3

## Run strategy tournament
../.venv/bin/python -u strategy_lab.py

## Walk-forward best eligible strategies
../.venv/bin/python -u walkforward_lab.py

Database: data/crypto_history.duckdb
Reports: reports/
