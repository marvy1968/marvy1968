# Pre-registered holdout test (written and committed BEFORE the holdout was scored)

Date: 2026-11-07. Holdout: all NBA 2026-27 games Oct 20 - Nov 6 2026 with Kalshi prop prices (132 games downloaded,
never looked at: no model output, Brier or ROI has been computed on them).

The one rule judged on the holdout:
- model: minutes model (models.v2_mean, parameters fit on 2023-24 + 2024-25 only, cached in
  v2_params_train_2024_2025.json), pregame anchor = the player's own earlier-game average (p_minutes in market_v3.py)
- contracts: Kalshi KXNBAPTS / REB / AST / 3PT, at the end of Q1, Q2, Q3
- tradeable: quote refreshed after the period ended, bid/ask gap <= 10c, bid >= 2c, ask <= 98c, rung not yet reached
- bet: buy NO only, at 1 - yes_bid, when (1 - p_model) - (1 - yes_bid) - fee >= 0.06; fee = 0.07 p (1 - p); one contract each
- success = positive ROI after fees AND a game-level bootstrap 90% range whose lower end is above -5%
  (with ~130 games the range will be wide; a positive point estimate alone is not enough to promote anything).

Everything else printed by market_v3.py --holdout (other models, sides, thresholds) is exploratory and is not the test.
