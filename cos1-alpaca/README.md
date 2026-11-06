# COS1 Alpaca

Paper-only stock bot. One process, three modes, an S&P 500 regime filter on SPY.

| Regime | Live behavior |
|---|---|
| Up (close above a rising 50/200, 20-day slope, ATR% inside the band) | Long trend entries |
| Down | Short trend entries, and only if the name is easy to borrow |
| Chop | No live trend entries. Range-box is log-only |

Live modules: day EMA pullback (2.75R target, ATR stop, break-even at +1R, then a staircase on the broker stop) and swing (EMA cross with a 2R target, plus a Donchian 55/20 expert at 0.5% NAV and a 1.5 ATR stop). Day entries stop at 15:00 ET. Day positions are flattened at 15:50 ET. Half-days use the same gaps before the early close.

Log-only unless you flip a config flag: scalp, VWAP opening-range break, VWAP reclaim, Velez momentum, mean reversion, range-box. Flags default off. There is no half-position scale-out and no 1R hard take-profit.

Orders exist only with `--paper`, and only at `https://paper-api.alpaca.markets`. No flag does not send orders. `--dry-run` logs signals. `rollout.live` is empty and a non-empty list is rejected. Data uses `feed=iex`.

```bash
python -m cos1_alpaca run                  # no orders
python -m cos1_alpaca run --dry-run        # log signals
python -m cos1_alpaca run --paper          # paper orders
python -m cos1_alpaca backtest --months 6  # short synthetic walk-forward
python -m pytest
```

`COS1_ENV_FILE` points at a file with `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. The process does not print those values.

Install: see `DEPLOY.md`. The systemd timer is dry-run only.

Cost model for the backtest is `btcosts.py` (`slip_bps`, `fee_bps_sell`, `commission`, `max_pos_frac`, feed `iex`), the same fields as the Alpaca research harness in this repo. Point `COS1_BTCOSTS` at another module that defines `Costs` and `Portfolio` to swap it in. The default run is synthetic and short (four names, last 3-6 months). It is not a claim of an edge.
