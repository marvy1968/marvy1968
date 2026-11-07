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

## Result (scored once, 2026-11-07, after the commit above)

Pre-registered rule (minutes model, NO only, edge >= 6c): 2,910 bets, ROI -0.0%, game bootstrap 90% range -4.5% .. +4.5%.
**FAILED.** It breaks even on the holdout. Full table: results/v3_dev_val_holdout.md.

Exploratory (chosen after looking, so not a test): ROI rises with the edge threshold for every model. Both sides,
edge >= 8c: minutes model +4.8% (4,130 bets, +0.6% .. +9.0%), v3 +6.1% (2,939 bets, +1.3% .. +10.8%).
Next test, written down now: v3, both sides, edge >= 8c, the same quote filters, on NBA games from 2026-11-07 onward
(forward data that does not exist yet). Promote only if it is positive with a 90% range above -5% after 60+ games.

## Second test of the lead, on 2025-26 games not yet downloaded (written 2026-11-07, before those games were fetched)

`seen_games_2025_26.json` lists every 2025-26 game whose Kalshi prices had been used by the time of the holdout result.
All other 2025-26 games from 2026-01-17 on (still downloading) have never been looked at.

Rule: v3 (market pregame anchor + minutes model, parameters fit on 2023-25), both sides, edge >= 8c after fee, tradeable
fresh quotes (gap <= 10c, bid >= 2c, ask <= 98c, refreshed after the period ended), one contract each.
Success: ROI > 0 with a game-bootstrap 90% range whose low end is above -5%, on at least 150 unseen games.
The forward test (NBA games from 2026-11-07 on, same rule, 60+ games) stays as written above.
