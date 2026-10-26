# 5-Expert Sector Scalper for Alpaca

Five independent scalping experts trade the top 5 stocks (by 20-day dollar volume) in each of the
11 S&P 500 sectors. A daily sector heat map (BULL / BEAR / SIDEWAYS) decides which direction each
expert may trade. Each expert holds at most 3 positions, each sized at 0.5% of total equity.

## Files
| file | purpose |
|---|---|
| config.py | all settings, sector ETF + candidate lists, per-expert overrides |
| indicators.py | causal indicators (VWAP, EMAs, ATR, RSI2, Bollinger, Keltner, squeeze, opening range) |
| regime.py | sector heat map, intraday overlay, heatmap.html, top-5 universe selection |
| experts.py | the 5 experts |
| common.py | Signal/Position types, regime->direction rules, time stop / breakeven / EOD rules |
| broker.py | Alpaca orders: bracket entries, close, move stop, shortability checks, restart recovery |
| data.py | batched bar downloads, on-disk cache, rolling live bar cache |
| bot.py | live / paper runner |
| backtest.py | backtester running the same expert + management code |

## Setup
```
pip install -r requirements.txt
export APCA_API_KEY_ID=...  APCA_API_SECRET_KEY=...
export APCA_PAPER=true          # default; set false only when you mean it
export APCA_FEED=iex            # or sip with a paid data plan
```

## Backtest
```
python backtest.py --start 2026-06-01 --end 2026-08-31 --equity 100000 --slippage-bps 2
```
Outputs in `bt_results/`: trades.csv, equity_curve.csv, regime_heatmap_history.csv, pnl_by_expert.png,
and summary tables by expert, expert x side, regime, sector and exit reason.

## Live / paper
```
python bot.py --dry-run   # signals only
python bot.py             # places bracket orders (paper by default)
```
`heatmap.html` refreshes every 15 min. Ctrl-C leaves positions protected by their broker-side brackets.

## The experts
| id | system | style | regime use |
|---|---|---|---|
| ORB | Opening Range Breakout (15-min) | trend | BULL long / BEAR short |
| VWAP | VWAP pullback | trend | BULL long / BEAR short |
| EMA | 9/20 EMA momentum pullback | trend | BULL long / BEAR short |
| RSI2BB | RSI(2) + Bollinger mean reversion | reversion | BULL dips / BEAR rips / SIDEWAYS both |
| SQZ | Bollinger-inside-Keltner squeeze | breakout | BULL long / BEAR short / SIDEWAYS both |

## Design notes
* One expert per symbol at a time: Alpaca nets positions per symbol, so two experts can't hold opposite sides.
* Orders are tagged `SB_<EXPERT>_<SYMBOL>_<id>` so a restart can tell which expert owns a position;
  positions without the tag are never touched.
* Shorts need >= $2,000 equity, shorting enabled, and an easy-to-borrow stock. Whole shares only.
* Qty = floor(0.5% x equity / price). Small accounts will skip expensive stocks (qty < 1).
* Max gross exposure = 5 experts x 3 x 0.5% = 7.5% of equity.

## Backtest caveats
* Universe is chosen once from data before `--start` (survivorship / fixed-universe bias).
* Fills at next-bar open +/- slippage; stops assumed hit before targets in the same bar.
* IEX live data covers only a small share of volume, so live signals can differ from SIP backtests.
  Backtest on the feed you'll trade (`--feed iex`) for an apples-to-apples check.

## Stress test (offline, no API keys needed)
```
python stress_test.py          # all 20 adversarial tests
python stress_test.py -k crash # one test
```
Covers look-ahead bias, fake edge on random data, same-bar stop/target, gaps, flash crash,
mark-to-market kill switch, dirty/sparse data, half days, DST, tick rounding, tiny accounts,
per-expert caps, corrupted state, failed closes, unfilled entries, manual positions, short rules, speed.

## Robustness (does the edge survive friction and luck?)
```
python robustness.py --start 2026-06-01 --end 2026-08-31          # slippage sweep + bootstraps
python robustness.py --trades bt_results/trades.csv               # bootstraps on an existing run
```
Slippage sweep at 0/2/5/10 bps with break-even bps per expert, trade-order shuffle drawdowns,
day-block bootstrap (P(losing period), drawdown percentiles), per-expert confidence,
dependence on the best 1%/5% of trades, first-half vs second-half stability, and a PASS/FAIL checklist.

## Breakeven stop fallback ladder (broker.move_stop)
1. price already through the new stop -> exit at market
2. modify the stop leg in place (429/5xx retried with backoff)
3. cancel both legs, WAIT for Alpaca to confirm and release the shares, submit a new OCO (new stop + old target)
4. OCO rejected -> plain stop; the bot then takes profit itself when price reaches the target
5. stop rejected -> close at market
If cancels are never confirmed, nothing new is submitted (avoids over-allocation); it retries next minute.
