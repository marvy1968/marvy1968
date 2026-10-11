# alpacabot: scalp, day-trade and swing strategies with an honest backtester

Status: **research only**. It downloads historical bars and backtests. It has no order-placement code on purpose: nothing trades, not even on paper,
until a strategy shows a positive out-of-sample result *and* the owner says so.

## Strategies
| Family | Strategy | Idea | Holding |
|---|---|---|---|
| scalp | `VwapReversion` | 1-min price stretched k sigma below session VWAP and the bar closes up: long back to VWAP, ATR stop, time stop | minutes |
| day | `OpeningRangeBreak` | first close above the 15/30-min opening range with price above VWAP, range-low stop, 1.5-3R target, flat by the close | hours |
| day (5-min) | `DualRegime` | ADX router: trend panel (EMA 9/21, MACD histogram, Parabolic SAR) when ADX >= 25, reversion panel (RSI 7/14, Bollinger touch, fast stochastic 8,3) below; 2 of 3 votes; virtual break-even / ATR-trail / ADX-scaled exits | minutes-hours |
| swing | `RsiPullback` | price above 200-day SMA and RSI(2) washed out: buy next open, sell on the first bounce, ATR stop | days |
| swing | `Breakout` | close above the 20/55-day high in an uptrend, exit on a 10/20-day low | weeks |

Long only. Shorting needs a margin account and borrow and adds costs this backtest does not model.

## The central brain (`brain.py`, family `brain`)
Ten experts (`experts.py`: Velez elephant bar and 180 reversal, Raschke Holy Grail, Brooks pullback, Connors RSI(2), Bollinger bounce, Williams %R, Donchian/Turtle breakout, VWAP deviation, and the Dual-Regime consensus engine) each decide when to **buy**, how to **sell** (own stop, target or indicator exit) and when to **take profit**. The brain controls what becomes a trade:
volatility circuit breaker (ATR14/ATR50 outside 0.3-2.5), ADX regime router (trend experts only in trends, mean-reversion experts only in ranges), 15-minute trend filter, relative-volume check on breakouts, optional 2-expert consensus, one position per symbol, a re-entry cooldown, a slot cap, and **1% of equity risked per trade** (`Portfolio(risk_pct=0.01)`).
`brain_walk_forward` tests every expert **alone on the train years**, keeps only those with positive expectancy and profit factor > 1.05, picks the brain settings on the same years, then runs the test years **once**, with per-expert attribution, placebo, cost stress and SPY.
It reports the average daily return and whether it reached the 0.75-1.0%/day target. **It never tunes toward that target**: that would be fitting the past.

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
    python -m alpacabot backtest --family regime
    python -m alpacabot backtest --family brain      # ~1-2 h; --placebo 20 --slip-bps 2
    python -m alpacabot backtest --family scalp

Results land in `state/reports/alpaca_<family>.json`. Through the vm-link: `alpaca download ...` / `alpaca backtest --family ...`.

## Execution safety layer (`execution.py`, paper only, tested offline with a fake broker)
Spread check before market orders, daily drawdown circuit breaker (flatten + 24h entry lock), stale-position time exits (default 45 min), ATR-normalized sizing,
no entries in the first 15 minutes or after 15:30 and flat by 15:55, retry with exponential backoff on 429/5xx/network errors, size-capped rotating logs,
and `live_decision()` which feeds today's bars to the *same* strategy object the backtest uses. The Alpaca adapter always builds a paper client; there is no live switch.

## Expectations
Most retail scalping and day trading loses after costs, and the free feed adds noise. The swing families are the most likely to survive. If the tests print `NO EDGE FOUND`, believe them.
