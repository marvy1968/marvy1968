# fx-experts

Five independent forex systems taken from five experts' books, for OANDA v20. They run in **practice (paper) mode by default**.

| File | What's in it |
|---|---|
| FX_EXPERTS.md | The experts and books, each system's exact rules, sources, and what is BOOK vs DEFAULT |
| OANDA_NOTES.md | API notes: orders, stopLossOnFill/takeProfitOnFill, rate limits, US FIFO, and the state of the VM's tokens |
| FOREX_NOTES.md | Sessions, spreads, pips, pair behaviour, news, gaps, rollover |
| reports/ | Backtests and path-model checks (BACKTEST_duka_h1.md is the main one) |
| deploy/ | systemd units, env example, install script, DEPLOY.md. Not installed. |

Code:
- `fxexp/strategies/*`: one file per system. Pure functions shared by the backtest and the live runner.
- `fxexp/backtest.py`: event engine with an intrabar path, spreads, slippage and financing.
- `fxexp/live.py`: runner with broker-side SL/TP.
- `fxexp/oanda.py`: client.
- `fxexp/sizing.py`
- `fxexp/scorecard.py`
- `fxexp/check.py`: read-only preflight.

Commands:
- Tests: `python -m pytest -q tests`
- Backtest: `FX_DATA=data/duka_h1 FX_TF=H1 FX_TAG=duka_h1 python run_backtest.py`
