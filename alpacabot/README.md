# alpacabot: scalp, day-trade and swing strategies with an honest backtester

Status: **research only**. It downloads historical bars and backtests. It has no order-placement code on purpose: nothing trades, not even on paper,
until a strategy shows a positive out-of-sample result *and* the owner says so.

## Strategies
| Family | Strategy | Idea | Holding |
|---|---|---|---|
| scalp | `VwapReversion` | 1-min price stretched k sigma below session VWAP and the bar closes up: long back to VWAP, ATR stop, time stop | minutes |
| day | `OpeningRangeBreak` | first close above the 15/30-min opening range with price above VWAP, range-low stop, 1.5-3R target, flat by the close | hours |
| swing | `RsiPullback` | price above 200-day SMA and RSI(2) washed out: buy next open, sell on the first bounce, ATR stop | days |
| swing | `Breakout` | close above the 20/55-day high in an uptrend, exit on a 10/20-day low | weeks |

Long only. Shorting needs a margin account and borrow and adds costs this backtest does not model.

## How the backtest avoids fooling you
* Signals use bars up to and including bar i; the fill is the **open of bar i+1** plus slippage (tested by truncation: signals computed on a prefix equal the prefix of the full-day signals).
* A bar that touches both stop and target counts as a **stop**. Gap-downs through a stop fill at the open, not the stop.
* Costs: slippage per side (default 2 bps, stressed at 5 and 10 bps), sell-side regulatory fees, commission-free. Scalping lives or dies on this number.
* Sizing: risk 0.5% of equity to the stop, 25% notional cap per position (no leverage), 4 concurrent positions, 2% daily loss limit. Optional pattern-day-trader rule (3 day trades per 5 sessions below $25k).
* **Walk-forward**: parameters are chosen on the early years only (ranked by Sharpe, 100+ trades required), then run **once** on the later years.
* **Placebo**: the same exits and the same number of trades per day, with random entry times. A real entry rule must beat these.
* **Benchmark**: buy-and-hold SPY over the test years. Beating cash is not enough.
* Verdict `PROMISING` needs: positive expectancy, Sharpe > 0.5, 100+ trades, still positive at 2.5x the slippage, and beating 90% of the placebo runs. Anything else prints `NO EDGE FOUND`.

## Run it (VM)
Put paper-account **data** keys in `/opt/marv-bot/.env` yourself (never in chat or git):
`ALPACA_DATA_KEY_ID=...` and `ALPACA_DATA_SECRET_KEY=...`  (free IEX feed; note IEX volume is a few percent of the market, so VWAP and volume are approximations).

    python -m alpacabot download --start 2016-01-01
    python -m alpacabot backtest --family swing_rsi
    python -m alpacabot backtest --family swing_breakout
    python -m alpacabot backtest --family day
    python -m alpacabot backtest --family scalp

Results land in `state/reports/alpaca_<family>.json`. Through the vm-link: `alpaca download ...` / `alpaca backtest --family ...`.

## Expectations
Most retail scalping and day trading loses after costs, and the free feed adds noise. The swing families are the most likely to survive. If the tests print `NO EDGE FOUND`, believe them.
