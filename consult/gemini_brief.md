# Brief for Gemini: our trading ideas are stale, what should we try?

Owner goal: consistent positive paper-trading gains, ultimate target +1% per day. Today both bots lose or break even.
Everything below is PAPER money. Safety rails stay on (see "Constraints"). Be specific and testable: every suggestion must be something
we can backtest on 1-minute / 1-hour bars (Alpaca stocks, OANDA forex) before it trades. Say what would prove it wrong.

## What we run
**Alpaca stocks (intraday + swing).** Intraday experts (long): connors_rsi2, vwap_dev, bollinger_bounce, williams_r, brooks_pullback,
opening_range_breakout; (short): dual_regime, connors_rsi2, brooks_pullback, bollinger_bounce, vwap_dev, raschke_grail. New Velez-style
scalper and day trader (shadowed, first live orders Monday). Filters in order: heat map, direction first, Weinstein stage, multi-timeframe,
reversal confirm, oversold gate, regime block, trend confirm, sizing. Swing: funnel, oversold dip buys at 10:00 ET, sector top-down.
Universe: 24 mid and small caps. Live paper record: 232 intraday trades, profit factor 0.77; trades held under 30 min lose, day holds
PF 2.83.
**OANDA forex (7 pairs: EUR/USD GBP/USD AUD/USD NZD/USD USD/JPY USD/CAD USD/CHF).** Runner experts: lien_h1 (Lien Fader), hybrid_h1
(range box + Fibonacci + swing zones), confirm_h1 (box breakout with candle/volume/ATR confirmation), boxmr_h1 (range mean reversion),
conf_d1 (RSI/stochastic/Bollinger confluence), cog_h4 (Pristine Changing of the Guard), retest_h1 (break and retest playbook).
Exits: 0.5R loss cut, 1.25R take-profit, exit after 10 minutes under water (retest_h1 has its own structure stops).
The owner's separate fx-experts bot shares the account and lost about $700 overnight; it now has a loss rail (0.25% NAV close).

## What we already tested and rejected (12-month backtests, bid/ask fills)
Range-reversal, plain breakout, reclaim, learned-zone (walk-forward) prediction of buy/sell zones, time-based exits, Pristine rules on 1-hour
bars, close-to-close momentum variants, many Velez setups (rbi, halt: no repair beyond next-bar confirmation). Only the break-and-retest
playbook passed (PF 1.42 on 12 months, 91 trades over 3 years, win 27%), and it has not fired live yet.
Honest edge so far: nothing consistently above break-even after costs. NFL/college betting models: high accuracy on heavy favourites,
roughly break-even ROI after vig.

## Constraints
Paper money only. 1% max position, account-wide -1% daily breaker, 10-minute under-water exit, same-expert 10-minute cooldown, stops at
the broker, flatten by 3:50 pm ET. No lookahead; walk-forward / held-out tests only; costs and spreads included.

## What we want from you
1. Five to ten NEW ideas (not variations of the above) with a one-line rule each, the market inefficiency it relies on, the data needed,
   and the test that would kill it. Rank by expected evidence, not by story.
2. Which of our current experts you would drop and why, from the structure of what they do.
3. Anything wrong in our method: ways our tests could be flattering the results, or why +1% a day is or isn't reachable here.
Do not promise returns. If the honest answer is that a retail edge at +1%/day is unrealistic, say what a realistic, positive target is.

## Also: help us make our backtests faster
Our walk-forward backtests are slow and they block the one-job-at-a-time VM queue. Examples: the `filters` family (every brain filter combination,
one-month test) took 71 minutes; `expert_breakdown` took 9 minutes; the forex session-pullback family took 32 seconds. The VM is a single
Google Compute Engine instance. The engine is Python + pandas: per-bar simulation (`alpacabot/engine.py simulate_day`, `fxbot/research.py run`),
bid/ask fills, 1-minute stock bars and 1-hour forex bars, parameter grids evaluated one point at a time.
Please suggest concrete speed-ups ranked by effort versus gain: vectorising the signal and exit logic, caching indicator frames across grid
points, multiprocessing across symbols or grid points, successive halving / early stopping of clearly losing grid points, using numba, and
smarter grid design. Show short code sketches where useful. Remember we need identical results to the slow path, so say how we should verify that.

## ROUND 2 (2026-10-26, after your first reply): results of your ideas, all held-out, bid/ask, with controls
1. **London-open fade of the Asian move (your idea 3), 7 FX majors, H1.** Best setting (move >= 0.7 daily ATR, target 1.0x the move): PF 0.93 / 0.92 /
   0.90 over 3 months / 12 months / 3 years; at 2x spread 0.66 / 0.69 / 0.78. Looser setting (>= 0.4 ATR): PF 0.66-0.79, -15% to -29%. Win rate 43-58% but
   the winners are too small. REJECTED.
2. **End-of-day squeeze (your idea 2), 15:10 long, ~35-minute hold, on our liquid stock list.** Strict filter fired 0-2 times in 3 months; looser filter
   12 / 13 trades (PF 0.26 train, 1.41 validate, 0.66 at 3x slippage). The no-filter control (every stock long at 15:10) lost: PF 0.59 / 0.44, avg
   -0.35R / -0.50R. INCONCLUSIVE then shelved (the base structure loses).
3. **Sector-confirmed momentum (your idea 1), 55 sector-ETF top holdings + 11 ETFs, 10:15 entry, hold to ~15:45, 3 months, 5-minute bars.** Validate PF:
   top 1 / ETF > 0.5% = 0.77 (28 trades), top 3 / ETF > 0.5% = 0.95 (84), top 3 / ETF > 0.25% = 0.72 (93); controls: top 3 with no sector filter 0.83,
   3 random stocks 0.82. The sector filter adds nothing; nothing beat the controls. REJECTED.
Also rejected earlier: an EMA20/50 8-12 NY session pullback on the 7 FX majors (3-year PF 0.83).

**What does earn money so far:** our multi-day swing positions (oversold dip buys entered at 10:00 ET, stops/targets at the broker; sector top-down). They are
all green today, but the sample is tiny (5 open positions). Everything intraday we have tested (about 15 experts and 8 ideas) has no edge after costs.

**Round 2 question:** please propose 5 NEW ideas for MULTI-DAY swing trading (hold 2-20 days) on liquid US stocks and sector ETFs (daily bars, 10 years of data
available, costs: ~2 bps slippage per side), and 3 for forex on H4 / daily bars. Same format: the rule in one line, the inefficiency, the data, and the test
that would kill it. Prefer ideas that are NOT mean-reversion on indicators or chart patterns. Also tell us which published, replicated anomalies you would
test first (for example post-earnings drift, 12-1 month momentum with a trend filter, sector rotation, turn-of-the-month, short-term reversal after
volume spikes) and what out-of-sample decay is typical for each.

## ROUND 3 (2026-10-26 evening): results of your round-2 swing ideas (held-out halves: train to 2020-12-31, validate 2021 on; 5 bps/side stocks, 2 bps ETFs)
- **A turn-of-the-month SPY:** train 60t PF 1.91 avg +0.48% vs control 6-day window +0.35%; validate 69t PF 1.62 avg +0.41% vs +0.31%. About +0.1% per trade over a random
  window, ~12 trades a year: roughly market drift, not a standalone edge.
- **B sector-ETF dual momentum:** lost to SPY buy-and-hold in both halves (train CAGR -5.2% vs SPY +21.0%; validate +8.1% vs +14.7%). REJECTED.
- **C 52-week-high breakout (+volume > 2x 50-day avg):** on today's top holdings of each sector ETF (survivorship-biased) train PF 2.05 / validate 1.89 vs no-volume control
  1.63 / 1.57. On the HINDSIGHT-FREE list (2016's 21 largest companies): train 60t PF 1.11 avg +0.19% vs control 164t PF 1.15 avg +0.31% (volume filter WORSE);
  validate 85t PF 1.73 vs control 1.59. Not robust: the first half is flat and the second half is the 2021-26 bull market. NOT ADOPTED.
- **D 12-1 momentum (top third monthly vs equal-weight universe):** current-holdings list: +0.27% / +0.27% per month better than the universe in both halves; hindsight-free list:
  train top third +0.26%/month vs universe +0.63%, validate +1.43% vs +1.28%. Inconsistent. NOT ADOPTED.
- **E NR7 breakout, 3-day hold:** PF 1.02 / 1.00 (current list), 0.90 / 1.04 (hindsight-free), below the every-day control. REJECTED.
Live paper update: our swing book has CLOSED 12 trades, 3 wins / 9 losses, -$1,167 (funnel 0/2 -$507, oversold dip buys 0/3 -$427); the 5 open positions are +$505. So
swing has not earned yet either. Intraday experts overall: 254 trades, PF 0.77; the long side PF 0.58, shorts PF 1.01.

**Round 3 question:** with no daily idea clearing a fair control, what is the most defensible way for a small account ($50k, 5-10 positions) to get a positive expected
return from US stocks in 2-20 day holds - and is a plain trend filter (e.g. hold SPY/QQQ only above the 200-day average, cash otherwise) plus modest sizing a better
benchmark for us to beat than anything we have built? Please also say how to size and what drawdown to expect, and which ONE test you would run next on daily bars with
a hindsight-free universe (give the exact rule and the kill criterion).

## ROUND 4 (2026-10-27 night): what we tested and changed since round 3
- **Your benchmark (index above its 200-day average, cash below) is now ON PAPER** in the swing runner: gate on new stock longs while SPY < 200-day, plus an ATR-sized SPY/QQQ
  sleeve (1 ATR = 0.5% of equity, max 30% each, broker stop 4 ATR). Backtest 2014-26: SPY Sharpe 1.00 / maxDD -20.2% vs buy-and-hold 0.90 / -33.8%; QQQ 1.02 / -22.5% vs 0.95 / -35.0%.
  The intraday stock bot is PAUSED (254 trades, PF 0.77).
- **Power earnings gap proxy (your round-3 test), hindsight-free 2016 large caps:** train 8 trades PF 4.03 (+2.05% avg), validate 11 trades PF 0.81 (-0.53%) vs control +0.34% / +0.53%;
  survivorship-biased list 23 / 25 trades, edge vs control -0.32% / +0.32%. Fails the +0.20% kill rule; far below 30 trades. NOT ADOPTED.
- **OANDA review (all 7 FX majors, bid/ask, our live exits cut 0.5R / out 1.25R; PF 12 months / 3 years at normal spread):** hybrid_h1 (range box + Fibonacci) 0.69 / 0.69; confirm_h1 0.79 / 0.80;
  boxmr_h1 0.68 / 0.64; conf_d1 (daily RSI/stoch/Bollinger confluence) 0.58 / 0.70; cog_h4 (3 red bars then a green bar, 4-hour) 0.70 / 0.71 (-30% / -66%); every one worse at 2x spread. Their earlier
  "passes" were single 3-month windows. We now trade only lien_h1 (Kathy Lien fader: 12 months PF 2.75 on 15 trades, 3 years 0.94) and the break-and-retest playbook retest_h1 (12-month PF 1.42, no live fill yet).
  The owner's separate fx-experts bot (williams-vbo, faith-turtle, tharp-random entries) has 11 open trades, all with broker stops, no targets, net long USD ~1.7x NAV; its paper P&L is negative.

**Round 4 question (forex, daily / 4-hour bars only):** what is the single most defensible FX test we can run with FREE data (OANDA candles + a public policy-rate series such as FRED) - carry
(rate differential) combined with a 100-200-day trend filter on the 7 majors, monthly rebalance? Give: the exact rule, the data source and series IDs, how to size across 7 pairs, how to avoid
the 2008/2015/2022 carry crashes, the held-out split you would use, a control (e.g. random-sign carry or the same trend filter without carry), and the kill criterion. If you think FX has no retail edge
at these costs, say so and say what we should do with the OANDA side instead.

## ROUND 5 (2026-10-27 early morning): forex carry result + the owner's forex optimization request
- **Your carry + 200-day trend test FAILED the kill rule:** 7 majors, FRED 3-month rates (35-day publication lag), top 3 by |differential|, 1 ATR = 0.5% equity. Train (2004-2015)
  CAGR +4.8% Sharpe 0.47; validate (2016-2026) -2.3% (no drag) / -4.0% (1.5%/yr drag), maxDD -42% / -46%. Controls: trend only -6.7%, carry only -2.5% (validate, with drag).
- **Owner's new request:** "test every expert day-trade and scalp method, adapt it to OANDA forex, keep only the proven; optimize every expert on every timeframe, change each
  expert at least 10 times until positive ROI". Two runs are queued:
  1. **expert_tune_fx:** our 10 existing experts x H1 / H4 / D x 13 pre-declared variants (own exits; R-based exits 0.5/1.25, 1/2, 1/1, 0.75/1.5, 1/3; 20>200 SMA trend filter; trend +
     look-left; no-chase; fade the 20-SMA slope; signals REVERSED with 1R/1R and 1R/2R). Chosen on the 12 months before the last 3, CONFIRMED on the last 3 months (unseen) at 1x and 2x spread.
  2. **methods_fx:** 12 book methods, one book setting each, nothing tuned: London and New York opening-range breakouts (M15), Raschke Holy Grail (M15/H1/H4), Raschke-Connors
     Turtle Soup (M15..D), Connors RSI(2) (H1/H4/D), Carter TTM Squeeze (M15/H1/H4), MACD + 200 EMA (M15/H1/H4), 8/21 EMA scalp with a 50 EMA filter (M5/M15), Williams volatility
     breakout (D, close-confirmed), floor-pivot S1/R1 bounce (M15/H1), Lien Double Bollinger (H1/H4/D), inside-bar breakout (H1/H4/D). PROVEN = profitable on the 12-month train AND the
     3-month test, still profitable on the test at 2x spread, >= 20 test trades, and better than random entries with the same exits (3 placebo seeds).
**Round 5 questions:** (1) Critique the protocol: is choosing the best of 13 variants on 12 months and confirming on 3 months enough, or should we demand more (e.g. a second unseen
window, a multiple-testing correction across 39 expert x timeframe pairs)? Give a concrete rule. (2) Which of the 12 book methods have we probably translated WRONG for FX (sessions, stop
placement, entry timing on close-confirmed bars instead of stop orders), and what is the correct FX translation in one line each? (3) Name up to 5 more day-trade or scalp methods with a
documented, replicated FX edge after realistic retail spreads, with exact rules - or say plainly that none exist for M5-M15 retail FX. Keep it short and concrete.
