# Apply top hedge funds to the Alpaca Brain — test plan, 2026-11-05

User question 2026-11-05: how to apply what top hedge funds do to our bots
and make the bot grow equity (more profitable). Analysis + pre-registered
test plan only. Nothing changed live. Brain kill switch stays ON until a
variant passes the bar below.

## Starting point (audit, 2026-11-05 ~07:00 CT, instance-stockbot)
- Brain: 306 trades, PF 0.74, net -39.5R, expectancy -0.129R/trade.
  Longs -50.2R, shorts +10.7R. Scalps <=30m exp -0.308R (n=180),
  day holds >30m exp +0.554R (n=52).
- MFE: only 26% reach +1R, 54% never past +0.5R. When peak >=1R,
  win 90-100%. Winners-turned-losers after >=1R: 2/180.
- Stop exits n=94: avg peak 0.49R -> final -0.72R. Stops inside noise.
- TP counterfactual, holdout n=72: fixed 1R -6.32R vs baseline,
  2.5R +1.67R, 3R +2.51R, BE-after-1R +1.00R,
  half@1R+BE -2.66R, half@1R+50% MFE trail -7.30R.
- Minute exit lab: nothing passed. Exits alone do not fix losing entries.

Equity grows only if expectancy after costs > 0, i.e. PF > 1 on
out-of-sample data. A TP tweak worth +0.01-0.03R/trade does not do
that by itself. Entry + stop placement is first-order.

## What is transferable, and what is NOT
Transferable (mainly Man AHL / AQR, documented mechanics):
- No fixed take-profit; let winners run, exit on signal fade.
- Volatility-scaled sizing: fixed dollar risk, size down when ATR rises.
- Portfolio limits over tight per-trade stops; stress / drawdown caps.
- Signal-decay / time exits, not price-target exits.
- Costs modeled before the trade; test at 2x costs.

NOT transferable — do not copy as Brain rules:
- Renaissance Medallion 50.75% win / many small signals: stat-arb /
  microstructure, no published entry rules.
- Citadel market making: earn spread, offload inventory — different business.
- Two Sigma ML factor forecasts: no entry/stop/TP disclosed.
- D.E. Shaw market-neutral stat-arb: no mechanics published.

## Five single-change tests, in order (one change at a time)
Each test: same days, same costs (test at 1x and 2x), same Brain entries
unless the test IS the entry change. Train / validation / holdout split
frozen before seeing results. Pass bar for adoption: PF >= 1.0 on BOTH
validation and holdout, beats baseline on both, beats random entries,
trade count does not collapse to a handful, and expectancy > 0 after costs.

### Test 1 — Drop the fixed 1R TP (highest priority, cheapest)
- Control: current exits.
- Variant A: no fixed TP, move stop to breakeven after +1R, trail /
  flatten 15:55 ET, wide disaster stop only.
- Variant B: fixed TP 2.5R. Variant C: fixed TP 3.0R.
- Why first: counterfactual already says 1R hurts (-6.32R) and
  2.5-3R / BE@+1R help (+1.00 to +2.51R on 72 trades).
  Full bar replay needed — peak-R approximation is low-medium confidence.
- Note: this conflicts with the locked TP horse-race arms (half at
  2R / half at 1R + BE remainder). Run the horse-race first, then
  this test — if the race confirms half-scale hurts, rethink the arms.

### Test 2 — Wider ATR stop + vol-scaled sizing
- Control: stop = nearer of 5-bar low / SMA20 - 0.5 ATR (Velez) or
  per-expert tight stop.
- Variant: stop = entry +/- 1.5 ATR, then 2.0 ATR in a second run.
  Dollar risk per trade FIXED: size = risk$ / stop distance, so a
  wider stop means smaller size, not bigger loss.
- Why: stops fire at avg peak 0.49R — they sit inside normal noise.
  AHL: "a stop might knock you out and then you lose your exposure."

### Test 3 — Time / signal-decay exit (instead of a tight stop)
- Variant: exit when the entry signal reverses, OR after N minutes
  with no progress past +0.5R (pre-register N, e.g. 60 min, before
  seeing results), keeping a wide disaster stop only.
- Why: fits the day-hold pocket (>30m, exp +0.554R). Scalps are
  mostly cost + noise; giving a trade time to work is the AHL idea
  applied intraday, capped by the 15:55 ET flatten.

### Test 4 — Entry filters (first-order, because entry is the leak)
- Variant A: no new entries after 15:00 ET (audit: -0.30R, n=28, thin).
- Variant B: longs gated harder / down-sized vs shorts
  (longs -50.2R, shorts +10.7R — side-specific edge, size each side
  separately). E.g. long risk 0.5x short risk, or long entries need
  an extra confirmation the short side does not.
- Variant C: only setups that historically hold >30 minutes
  (day-hold pocket) — gate new entries to that pocket.
- Test A, B, C separately, not stacked. A stacked AND-filter that
  gives 0-5 trades is overfiltering, not success.

### Test 5 — Portfolio drawdown de-risking (lowest priority)
- Variant: cut size in steps as account drawdown deepens, with a
  pre-registered rule for scaling back in.
- Why last: Brain is kill-switched, drawdown is small, a 2% day
  breaker + kill switch already exist, and Aug 2007 shows de-risking
  mid-shock can lock in losses and miss the rebound. Do not start here.

## Guardrails for all five
- Paper only. Brain kill switch stays ON. No live money.
- One change at a time; no stacking filters in a single test.
- Backtest on SIP / Alpaca bars with realistic fills and slippage,
  at 2x costs as well as 1x.
- Keep stock bots separate from betting bots.
- A variant that only wins on discovery, or only on one half, is
  rejected — same rule as the SPY gates (Trend 0.40 -> 0.33,
  Top-Down 1.51 -> 1.46, SPY-vs-open failed; all rejected, no
  second guessing).
- If no variant passes, the correct answer is still "keep it off"
  — not another tweak. Entry is the bottleneck; further exit tuning
  alone is low-value.

## Forwardable note to Claude
> Please run, in order, the five single-change tests in
> hedge-fund-brain-plan-2026-11-05.md on the Brain, starting with
> Test 1 (drop fixed 1R TP: no-TP + BE@+1R vs 2.5R vs 3R, full bar
> replay) and Test 2 (wider 1.5/2.0 ATR stop + vol-scaled sizing,
> fixed dollar risk). Pre-registered pass bar: PF >= 1.0 on both
> validation and holdout, beats baseline on both, beats random,
> trade count does not collapse. Brain stays kill-switched until a
> variant passes. Research only, paper only.
