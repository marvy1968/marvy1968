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
