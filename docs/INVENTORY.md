# Strategy inventory (generated from the repository and the test results, 2026-10-27)

Source of truth: this repo plus the VM-link result files. Every number below was read from code comments or from a result file produced in this
work. If a system is not listed, it is not in the repo and nothing is claimed about it (SuperBot v1-v6, Moonbot1, Quantum Stock Bot, Dark Money +
News AI, CORE_N27, the crypto laboratory, FX-SNIPER 8.x, G3-B5 are NOT in this repository). "Backtest" figures are historical, after costs as stated.
Nothing here is a live profit claim. Paper trading started 2026-10-12.

## A. Alpaca swing book (live on paper, daily bars, `alpacabot/swing_paper.py`, rules in `alpacabot/zones.json` "swing")
| System | Rules | Status | Evidence |
|---|---|---|---|
| Sector Top-Down (`strategies.SectorTopDown`) | close > EMA20 > SMA50 and 20-day return beats SPY's by >= 3 pts; stop EMA20 - 1 ATR; exit close < EMA20; ranked by 20-day RS | LIVE paper, 0.5% risk x 5 slots (since 2026-10-27; was 0.35% x 3) | `swing_exits_base` 2016-mid 2025, 10 bps/side: today's list PF 1.54 (9/11 yrs > 1), last 12 months 1.21; hindsight-free 2016 list PF 1.33, 1.28. `topdown_sizing`, `topdown_rank`, `credit_gate` below |
| Stock Funnel (`funnel.py`, zones.json swing.funnel) | stage-2 + 2-of-4 votes, take-profit 1.25R | LIVE paper, weak | own exits 2016-2025: PF 0.85 (4/11 yrs), hindsight-free 0.98; positive since 2023 |
| Oversold confluence (swing.oversold) | majority of 4 oversold readings + volume, 10:00 entry, exit close > 20-day avg or 1.25R | EXITS ONLY since 2026-10-27 (`new_entries` false) | PF 0.56 over 2,877 trades, 9/11 years losing; hindsight-free 0.48 |
| 200-day index rule (SPY/QQQ sleeve + gate on new longs) | hold while close > 200-day SMA; gate blocks new longs while SPY below it | LIVE paper since 2026-10-26 | 2014-2026: SPY Sharpe 1.00 maxDD -20.2% vs buy-and-hold 0.90 / -33.8%; sleeve own-exit PF 2.85 (8/11 yrs). The gate on Top-Down is not separately backtested |

## B. Alpaca intraday (PAUSED, KILL_SWITCH, since 2026-10-26; `alpacabot/experts.py`, `brain.py`)
11 expert engines in code: velez_elephant, velez_180, raschke_grail, brooks_pullback, connors_rsi2, bollinger_bounce, williams_r, donchian_turtle,
vwap_dev, dual_regime, opening_range_breakout (+ trend_stairs, scalper families in research). Live paper record 2026-10-12..27: 254 trades, 106W/148L,
profit factor 0.77, longs PF 0.58, shorts PF 1.01 (`alpaca paper-report`). Cancelled-by-record sides are listed in `brain_config.json`.
Velez: 18 setups in code (rbi, halt, nrb, vbs, bt, trap, bull180, 2020, clear, pause, gift, orb, ldb, mortgage, flat20, flat200, gbr, brk); net -$278 live
and every 3-month backtest lost (brain_config note); velez_rbi / velez_halt are shadow-only. Every intraday idea tested on mid and small caps also lost.
Mid/small-cap tests, scalper, ladder variants: see `state/alpaca/reports` on the VM and `daily_log` entries.

## C. Alpaca research systems (not live)
Book A (55/20 Donchian, 1.5 ATR stop) and Elite-20 (-2.5 sigma reclaim, MA50 target): `strategies.BookA / Elite20`, pre-declared test in `research.py`
(comment at line ~483). Not in the live bots. Other pre-declared tests this stretch, all REJECTED: breadth gate, 14 swing exits (`swing_exits`), vol-targeted
200-day rule, HYG/IEF credit gate (`credit_gate`: Sharpe +0.03 / -0.04), 90-day vol-adjusted ranking (`topdown_rank`: Sharpe -0.30 / -0.10),
daily anomalies, sector momentum, EOD squeeze, power gap. ADOPTED: `topdown_sizing` 0.50% x 5 slots (confirm years Sharpe 0.70 -> 0.85 / 0.13 -> 0.65, maxDD -10.2% / -8.5%).

## D. OANDA runner (`fxbot/live.py`, practice account)
Trades: lien_h1 (Lien fader, 20-pip break; 12-month PF 2.75, 3-year 0.94) and retest_h1 (Break & Retest playbook, `fxbot/playbook.py`; no live fills yet).
Log-only (signals logged and graded): f0_h4, box_h1, hybrid_h1, confirm_h1, boxmr_h1, boxcons_h1, boxf0_h4, conf_d1, conf_h4, cog_h4 (12-month / 3-year PF:
hybrid 0.69/0.69, confirm 0.79/0.80, boxmr 0.68/0.64, conf_d1 0.58/0.70, cog_h4 0.70/0.71). Tests with no pass: `expert_tune_fx` (13 variants x 3 timeframes),
`methods_fx` (12 book methods; only inside-bar H4 passed the first bar), `strict_fx` (3 finalists rejected), `fx_exits` (15 exits x 11 strategies),
carry + trend (validate CAGR -2.3% / -4.0%). Runner rails: daily breaker 2%, 0.5R cut / 1.25R target / 10-minute exit, profit lock (blocks new entries after +0.4%
and a 35% giveback, since 2026-10-27).
The fx-experts bot (williams-vbo, faith-turtle, tharp-random, lien-zeros; separate bot, 9 open trades) has NOT been through our tests. Rails we added: a broker stop,
a close at -0.25% of NAV, and a per-trade profit lock (stop moved to keep half the best gain once +0.2% of NAV).
Eight redacted copies of the owner's forex bots are in `forex_bots/` (README there). MicroBeast appears only inside two Velez staircase scripts.

## E. Betting / predictions (out of scope here, see CLAUDE.md)

## F. Unresolved items
- fx-experts bot strategies untested by us; USD exposure about 1.7x NAV; stops far away (USD/JPY about 200 pips).
- Swing book live sample is small (13 closed trades before 2026-10-27).
- Forex runner: whether to switch it off entirely is still the owner's decision.
