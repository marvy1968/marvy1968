# In-game basketball prop pacer: design + walk-forward backtest (for Grok)

Nov 7 2026. NBA, NCAA men's, NCAA women's. All results below come from a season that no model saw in training
(train 2023-25, test 2025-26), and for the NBA from real Kalshi prices at the quarter breaks.

## TL;DR

1. **A straight-line pacer loses money and should be retired.** "Stat so far × game length ÷ minutes played" is the
   worst model in every league, and against Kalshi prices it lost **−8.2%** after fees on 1,176 bets. Its
   90% range was −14% to −3%, so this is not bad luck. When it says 73% the outcome happens 53% of the time: it
   overrates hot starts.
2. **The fix is to project minutes, not the rate.** A hot or cold first half barely predicts the rest of the game.
   The fitted weight on tonight's per-minute rate after 24 minutes on the floor is only about 15% for points (NBA).
   What actually moves a player's final line is **remaining minutes**: blowouts, foul trouble, whether he is on the
   floor, and his normal rotation share.
3. **The minutes model** (8 fitted numbers) and a gradient-boosted version of it are the best in all three leagues,
   with near-perfect calibration on the unseen season.
4. **Against Kalshi (NBA, 68 games, Jan-May 2026):** the minutes model made **+8.8%** after fees on 469 bets at
   an edge of 6c or more. The 90% range was −1.8% to +17.8% and the chance of truly positive ROI is 92%. **That is a
   lead, not proof**: on the earlier half of that sample, used to choose settings, it was about break-even.
   The profit is in **NO / Under bets (+13%)**; YES bets lost (−6%).
5. Most Kalshi in-game prop quotes are untradeable: only 22% had a bid/ask gap of 10c or less at the breaks.

## The minutes model (`models.py: v2_minutes, v2_mean`; called "v2" in code)

At a break, for one player and one stat:

```
share       = (1 - w) * pregame_minutes/game_length + w * minutes_so_far/elapsed,   w = elapsed / (elapsed + tau)
rem_minutes = share * minutes_left * foul_factor * blowout_factor * (bench if not on the floor at the break)
rate        = (1 - v) * pregame_per_minute + v * tonight_per_minute,                v = minutes_played / (minutes_played + kappa[stat])
rem_mean    = rate * rem_minutes * (tonight's team pace / usual pace) ^ pace_beta
P(final >= line) = negative binomial with variance = phi(mean) * mean, phi fitted on train residuals
```

`foul_factor = exp(-foul * fouls above (foul_out - 1) * fraction_played + 0.5)`, and 0 when fouled out.
`blowout_factor = exp(-blow * max(|margin| - 12, 0)/10 * fraction_played/0.75 * pregame_share)`.
Pregame anchors are each player's exponentially weighted averages (half-life 10 games) from **earlier games only**.

Fitted parameters (Poisson deviance, train seasons):

| | tau | kappa pts | kappa reb | kappa ast | kappa 3PM | foul | blow | pace_beta |
|---|---|---|---|---|---|---|---|---|
| NBA | 32.8 | 144 | 186 | 41 | 244 | 0.13 | 0.42 | 0.17 |
| NCAA men's | 34.1 | 93 | 97 | 142 | 168 | 0.18 | 0.00 | 0.32 |
| NCAA women's | 36.9 | 93 | 101 | 145 | 165 | 0.31 | 0.08 | 0.31 |

A large kappa means "trust the pregame rate". For points, rebounds and threes the model gives tonight's rate little weight.
Assists (NBA) are the exception: they follow tonight's role faster.

## Results (test season 2025-26; full tables in `results/`)

Average miss on the player's **remaining** points:

| | naive pacer | Claude Prop 1 (live now) | minutes model | ML |
|---|---|---|---|---|
| NBA after Q1 | 7.30 | 4.71 | 4.21 | 4.20 |
| NBA after Q3 | 2.58 | 2.37 | 2.33 | 2.29 |
| NCAA men's at half | 3.38 | 2.95 | 2.86 | 2.83 |
| NCAA women's after Q1 | 5.38 | 3.60 | 3.34 | 3.43 |

Calibration, NBA points (predicted → actual): naive 0.55→0.42, 0.73→0.53, 0.84→0.64; minutes model 0.55→0.57,
0.75→0.75, 0.85→0.83.

Kalshi NBA (`results/nba.md`, `results/nba_kalshi_bootstrap.txt`; game-level bootstrap, whole sample):

| model | bets (edge ≥ 6c) | ROI after fee | 90% range | P(ROI > 0) |
|---|---|---|---|---|
| naive pacer | 1,176 | −8.2% | −14.0% .. −2.7% | 1% |
| Claude Prop 1 | 856 | −6.7% | −14.5% .. +0.7% | 7% |
| minutes model | 469 | +8.8% | −1.8% .. +17.8% | 92% |
| ML | 592 | +6.3% | −3.3% .. +14.2% | 87% |

Probability score (Brier) on the evaluation part: Kalshi mid 0.164, naive 0.171, minutes 0.132, ML 0.130,
ML + market blend 0.129. Slices of the minutes model at 6c (small, post hoc, treat as leads): NO +13% (350 bets),
YES −6% (119); rebounds +31%, threes +26%, points +3%, assists −9%; Q1 +8%, Q2 +13%, Q3 +2%.

## Hand-set rules in `pacer_v2/` vs what happened

Starters' remaining minutes at the last break, relative to a close game with few fouls:

| situation | pacer_v2 | NBA | NCAA men's | NCAA women's |
|---|---|---|---|---|
| one foul under the limit | ×0.50 | ×0.82 | ×0.75 (n=17) | ×0.81 |
| two fouls under | ×0.80 | ×0.97 | ×0.91 | ×1.04 |
| margin 18-25, late | ×0.65 | ×0.62 | ×0.94 | ×0.87 |
| margin 25+, late | ×0.40 | ×0.28 | ×0.88 | ×0.74 |

Suggested change: replace these constants with the measured ones, per league. College blowouts barely cost minutes.

## Recommendation

- Use the minutes model (or ML) to produce `p_model_raw` for `pacer_v2/`, instead of the straight-line pacer.
- Paper only, NO side first, edge of 6c or more after fee, bid/ask gap of 10c or less, and only quotes refreshed after the period ended.
- Promote only after 100+ graded live paper bets beat break-even with positive closing value.
  The +8.8% has a 90% range that includes zero.
- Before using it on Bovada: Bovada's live lines are a different market. Run the same test on Bovada lines from
  `state/live_props/shadow.jsonl` once enough are logged.

## Reproduce

```bash
# data (public): sportsdataverse ESPN play-by-play
#   espn_nba_pbp/play_by_play_{2024,2025,2026}.parquet
#   espn_mens_college_basketball_pbp / espn_womens_college_basketball_pbp play_by_play_{2025,2026}.parquet
python research/prop_pacer/build.py nba_snap.parquet nba PBP_2024 PBP_2025 PBP_2026       # ~7 min
python research/prop_pacer/fetch_kalshi.py nba_snap_2026.parquet kalshi_breaks.jsonl 120 7  # ~2.5 h, ~3 req/s
PACER_SAVE_MARKET=market_rows.parquet python research/prop_pacer/evaluate.py nba_snap.parquet kalshi_breaks.jsonl out/
python research/prop_pacer/bootstrap.py market_rows.parquet
```

Checks done: rebuilt points equal the final score in every sampled game, and minutes sum to 240 per NBA team and 200 per
college team (OT included). Kalshi settlement agrees with the play-by-play box score on 100% of matched contracts.

Limits: one test season; Kalshi covers 68 games after quote filters; quotes are 1-minute candle closes, not a
fill simulation (no queue, size or slippage); college has no exchange props, so college results are accuracy only.
