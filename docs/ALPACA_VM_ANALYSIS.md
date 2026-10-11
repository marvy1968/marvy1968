# Alpaca bots on the VM: what exists, what the results say (read 2026-10-11, secrets scrubbed)

## Inventory (home folder of moonrider1968)
| Where | What | State |
|---|---|---|
| `scalp_bot/` | Research harness: 5 intraday "experts" (1-min), Weinstein Stage 2 weekly breakout, Velez swing, 365-day volume-zone strategy, news/sector engine, `stress_test.py`, `README.md`, `RESEARCH_LOG.md` | Last runs Sep 25-30. `bot_log.txt` (Oct 9): `venv/bin/python3: not found` x7, so the scheduled job is failing |
| `alpaca_master_alliance/` | "Part 2c-2j": replay of 6 experts as 5 alliances, walk-forward + Monte Carlo qualification, regime engine, trade manager, a paper runner (`run_part2j.py`, `paper_only: true`, risk 0.5%, daily loss 1.5R, 6 positions) | Replay Mar 18-Jul 10 2026; runner heartbeat last written Jul 17 |
| `MasterBot_Center/Trading_Alpaca/` | `velez_master_sniper_all.py` (Alpaca paper), `alpaca_beast.py`, `alpaca_sniper.py` (heartbeat stub: "Logic discussed goes here"), sector gatekeeper, `config.py` (sector trend bot) | Logs to Apr 15; 432,415-line `velez_master_sniper.log` |
| `bots/killer_beast_sniper/` (+236 archived files), `oanda_active/`, `crypto_bot.py`, `unified_trading_bot.py`, `stock_daemon_v3_2_production.py`, `alpaca_stock_weinstein_hybrid_v3.2.py`, `alpaca_swing_velez_weinstein.py`, ... | Older variants and one-offs | `alpaca-stock-expert.service` is loaded but inactive (dead) |

## What the results say (their own numbers)
* **Weinstein Stage 2 breakout (the only strategy that passed discovery)**: discovery 2017-21 t = 3.84 (+0.59R, 13.9%/yr, DD -21.4%). Frozen rules, run once on the held-out 2022-Sep 2026 with the market filter: **124 trades, +0.10R, t = 1.03, CAGR 2.43% vs buy-and-hold SPY 12.17%, max DD -20.8% vs SPY -24.5%; by year -10.3%, +2.6%, +3.9%, -0.3%, +4.2%**. The log's own pass rule (t >= 2.0, smaller drawdown than SPY, not one year) fails on t. Verdict from their rule: no edge, stay with the index. Also flagged in the log: the universe is stocks listed today (survivorship bias flatters even this).
* **Scalp experts (5 intraday + zones)**: -$0.145 per trade; zones t = 1.05; Velez swing arms t = -4.18 / -4.06. All rejected by the log.
* **Master Alliance replay (1,780 trades, 39 symbols, Mar-Jul 2026)**: combined **-92.6R**. No alliance qualified: raschke+brooks +0.031R on 52 trades (needs +0.10R, PF 1.25, 60% walk-forward positive); velez+raschke -0.047R on 1,529 trades (PF 0.90, Monte Carlo profitable 2.9%); overall Sharpe -2.2.
* **Trading_Alpaca paper bot**: `trades.csv` shows the same symbol entered twice 16 seconds apart (META 106 then 79 shares, Mar 31): a duplicate-order bug. `config.py` sets `RISK_PER_TRADE = 0.05` (5% of equity at risk per trade, 10 positions, 4R daily loss cap = up to 20% a day) next to a $10,000 starting equity (also under the $25k pattern-day-trader rule).
* `scalp_bot/backtest_raw.log` prints a tidy "2017-2021, 10 symbols, 42 trades, t = 2.38 (Significant edge), 21.4% annualized" summary that matches nothing in the rigorous runs (different universe, different t); treat it as unreliable.

## Reading
The research discipline here (pre-registration, frozen held-out test, noise and look-ahead stress tests) is good and it is doing its job: it rejected almost everything, including the one strategy that looked best in discovery. Every intraday/scalp variant has negative expectancy after costs; the swing result did not hold out of sample. The live-style bots (sniper, beast, sector trend) have no validation behind them and have known risk-sizing and duplicate-order problems.

## Design consequences for `alpacabot/`
1. Keep the protocol (train/test split, placebo, cost stress, benchmark) and make it stricter: t-stat on trade returns, test period untouched until the rule is frozen, survivorship-free universe (liquid ETFs and index members at the time).
2. Position sizing 0.25-0.5% risk per trade, not 5%. Daily loss limit 1-2%. No more than 4 positions.
3. Idempotent orders (client order ids) so a restart cannot double-enter; state persisted to disk; paper only until a strategy passes.
4. Benchmark every result against buy-and-hold SPY, not against zero.
