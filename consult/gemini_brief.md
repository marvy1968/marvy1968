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
