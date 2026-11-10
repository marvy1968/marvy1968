# H2H Champion Comparison — Frozen Spec (2026-11-08)

## Interpretation

The user asked "How about the champion comparison" after the venue-split
check. The champion factor was tested only as an add-on to the H2H total in
the first run (and dropped because it added nothing), and it was never tested
on the NCAAW arms or the NBA validation season. This test covers both gaps:

- **Part 1** — champion votes as a STANDALONE pick rule, on the data already
  on disk (men's college Arm A, men's postseason Arm B, NBA Arm C).
- **Part 2** — champion factor with vs without, on the datasets where it was
  never tested: the NCAAW arms (this season + 2026 tournament) and the NBA
  2025-26 validation season.

## Frozen metric list (14 metrics, same list as the first H2H spec)

Team season averages, per game. Direction = which side "wins" the metric.

| # | Metric | Higher wins? |
|---|--------|--------------|
| 1 | Points per game | higher |
| 2 | Rebounds per game | higher |
| 3 | Offensive rebounds per game | higher |
| 4 | Defensive rebounds per game | higher |
| 5 | Assists per game | higher |
| 6 | Steals per game | higher |
| 7 | Blocks per game | higher |
| 8 | Turnovers per game | LOWER |
| 9 | Fouls per game | LOWER |
| 10 | Three-pointers made per game | higher |
| 11 | Field-goal % | higher |
| 12 | Three-point % | higher |
| 13 | Free-throw % | higher |
| 14 | Assist / turnover ratio | higher |

Excluded deliberately: opponent points per game (not available in the core-API
season stats used for the tournament arms); pace (a style, not a quality
metric). Assist/turnover ratio: assists / max(turnovers, 0.1) computed from
the averaged assists and averaged turnovers.

## Champion-benchmark rule (frozen)

For each of the 14 frozen metrics, the benchmark is the average across the
last 3 champions' season averages on that metric. A team earns +1 champion
vote per metric where its pre-game average meets/beats the benchmark in the
good direction: >= the benchmark for higher-is-better metrics, <= the
benchmark for lower-is-better metrics. Tying the benchmark counts as meeting
it (+1). Both teams can earn the champion vote on the same metric, or neither
can. Maximum 14 champion votes per team per game.

## Lookahead rule (frozen)

The benchmark for a game uses only champions whose seasons were completed
BEFORE that game:
- Arms on the 2026-27 season (men's Arm A, NBA Arm C, NCAAW Arm A): champions
  from 2024, 2025, and 2026.
- Arms on March 2026 postseason (men's Arm B, NCAAW Arm B): champions from
  2023, 2024, and 2025 (the 2026 champion had not been decided yet).
- NBA 2025-26 validation season (Oct 2025–Apr 2026): champions from 2023,
  2024, and 2025 (the 2026 champion had not been decided yet).

## Champion identities (from ESPN scoreboard data, NOT prior knowledge)

Identified by the exploration script `identify_champions.py` (2026-11-08):
the championship-game winner (latest postseason game on the final date) for
college, and the winner of the last Finals game for the NBA.

- NCAAB: 2023 UConn Huskies (id 41), 2024 UConn Huskies (id 41),
  2025 Florida Gators (id 57), 2026 Michigan Wolverines (id 130).
  (Recorded in the first H2H run, from ESPN data.)
- NBA: 2023 Denver Nuggets (id 7), 2024 Boston Celtics (id 2),
  2025 Oklahoma City Thunder (id 25), 2026 New York Knicks (id 18).
  (2024/2025/2026 recorded in the first H2H run, from ESPN data; 2023
  Denver identified in this run: winner of the last Finals game, Jun 12
  2023, Denver 94 – Miami 89.)
- NCAAW: 2023 LSU Tigers (id 99), 2024 South Carolina Gamecocks (id 2579),
  2025 UConn Huskies (id 41), 2026 UCLA Bruins (id 26).
  (Identified in this run from ESPN scoreboard data: 2023 final Apr 2,
  LSU 102 – Iowa 85; 2024 final Apr 7, South Carolina 87 – Iowa 75;
  2025 final Apr 6, UConn 82 – South Carolina 59; 2026 final Apr 5,
  UCLA 79 – South Carolina 51.)

Champion sets per arm:
- Men's Arm A (2026-27): NCAAB 2024, 2025, 2026.
- Men's Arm B (Mar 2026 tournament): NCAAB 2023, 2024, 2025.
- NBA Arm C (2026-27): NBA 2024, 2025, 2026.
- NCAAW Arm A (2026-27): NCAAW 2024, 2025, 2026.
- NCAAW Arm B (Mar 2026 tournament): NCAAW 2023, 2024, 2025.
- NBA 2025-26 validation: NBA 2023, 2024, 2025.

Champion season averages: ESPN core API, season type 2 (regular season),
for the championship season. Per-game averages taken from the core API's
avg* stats where present, otherwise total / gamesPlayed. ast_to from the
core API's assistTurnoverRatio where present, otherwise
assists / max(turnovers, 0.1). Same parsing as the first H2H run.

## Part 1 — Champion votes as a STANDALONE pick rule

- Data: the 360 games already on disk in
  `h2h_backtest_games.csv` (men's Arm A 170, Arm B 67, NBA Arm C 123),
  whose champ_home / champ_away columns were computed in the first run from
  the same frozen benchmark rule. Per-game pre-game averages are
  reconstructed from the cached ESPN summaries (h2h_cache/summary_*.json)
  and cached core season averages (h2h_cache/core_*.json) so the
  per-metric champion votes can be reported; no scoreboard re-fetch.
- Pick rule: pick the team with MORE champion votes. Tied champion votes
  = no pick. No H2H votes, no home-court point.
- Report per arm AND pooled: games, picks made, wins, losses, ML win rate.
- Baselines (same as the first run): home-team win rate over the same
  evaluated games for Arms A and C; higher-seed win rate (games where the
  seeds differ) for Arm B.
- Per-metric champion win rate: for each of the 14 metrics, over evaluated
  games where exactly one team met the benchmark on that metric (decisive),
  the fraction where the benchmark-meeting team won the game. Report best
  and worst per arm and pooled.
- Overlap with H2H votes: (a) agreement rate — fraction of games where the
  standalone champion pick equals the H2H-only pick (where both pick);
  (b) correlation between the champion vote difference
  (champ_home − champ_away) and the H2H vote difference
  (h2h_home − h2h_away) across games; (c) how often the two picks disagree
  and which one was right in those games.

## Part 2 — Champion factor with vs without (datasets never tested)

- (a) NCAAW arms from `h2h_backtest_ncaaw_games.csv`: Arm A (2026-27,
  180 games) and Arm B (2026 tournament, 67 games).
- (b) NBA 2025-26 validation season from
  `h2h_backtest_nba_2025_26_games.csv` (1,215 games).
- Champion votes per game are computed in this run from the reconstructed
  per-game pre-game averages (cached summaries for Arm A / NBA validation;
  cached core regular-season averages for the tournament arms) against the
  benchmarks above. Champion season averages for the newly identified
  champions (NCAAW 2023/2024/2025/2026, NBA 2023) are fetched from the
  core API and cached; all other champion averages reuse the first run's
  cache.
- Totals: WITH champion = H2H votes + champion votes + home point
  (+1 home team on non-neutral games in the regular-season arms; no home
  point in the tournament arms). WITHOUT champion = H2H votes + home point
  (the H2H-only totals already in the CSVs — recomputation must reproduce
  them exactly as a self-check).
- Report per arm: picks, wins, ML win rate WITH the champion factor and
  WITHOUT it, and the baseline (home-team win rate for Arm A / NBA
  validation; higher-seed win rate for NCAAW Arm B).

## Part 3 — POWER RANK vs the champions (added 2026-11-08, user refinement)

The binary +1-per-metric champion vote throws away magnitude: a team 0.1%
above the champion benchmark gets the same +1 as a team 10% above. The
power rank keeps the magnitude.

- For each of the 14 frozen metrics, compute the team's normalised
  difference from the champion benchmark:
  - Higher-is-better metrics: (team_avg − benchmark) / benchmark.
  - Lower-is-better metrics (turnovers, fouls):
    (benchmark − team_avg) / benchmark.
  A positive value means the team is above champion level on that metric;
  a negative value means below it.
- The team's POWER SCORE is the mean of those 14 normalised differences
  (a single number; positive = above the champion standard on average,
  negative = below it).
- Pick rule: the team with the higher power score is the pick. Scores tied
  within 0.001 = no pick. No H2H votes, no home-court point — the power
  score alone.
- Datasets: the SAME datasets as Parts 1 and 2 — first run's three arms
  (men's Arm A, men's Arm B, NBA Arm C), the NCAAW arms (Arm A + Arm B),
  and the NBA 2025-26 validation season. Same champion benchmarks and the
  same lookahead rule as Parts 1 and 2 (benchmarks from champions whose
  seasons finished before the game).
- Baselines: same as Parts 1 and 2 — home-team win rate for the
  regular-season arms (men's Arm A, NBA Arm C, NCAAW Arm A, NBA
  validation); higher-seed win rate for the tournament arms (men's Arm B,
  NCAAW Arm B).
- Report per arm AND pooled: games, picks, wins, ML win rate vs the
  baseline. Direct comparison to the binary champion-vote rule from
  Part 1 (per arm: power-rank win rate vs binary-vote win rate on the same
  games — does the continuous power rank beat the binary vote?).
- Power-score distribution: how many teams (across all evaluated games)
  are actually above the champion standard at all (power score > 0), the
  mean/median power score per arm, and the range.
- Margin analysis: split the picks by the margin between the two teams'
  power scores (the gap) into quartiles and report the win rate in each
  quartile — does a bigger power-score margin predict a higher win rate?
- Verdict: a plain statement on whether the power rank has signal where
  the binary vote did not. If it has no signal, say so plainly.

## No-lookahead rules

- Pre-game averages use only games played on strictly earlier dates
  (regular-season arms) or the pre-tournament regular season only
  (tournament arms, core API type 2). No tournament game contributes to
  any average. The game being predicted never contributes to its own
  averages.
- Champion benchmarks use only champions whose seasons were completed
  before the game (see lookahead rule above).
- The pick for a game is computed only from data available before that game.

## Data sources

- Existing game lists and results: `h2h_backtest_games.csv`,
  `h2h_backtest_ncaaw_games.csv`, `h2h_backtest_nba_2025_26_games.csv`.
- Cached ESPN summaries: `h2h_cache/summary_*.json` (men's + NBA) and
  `h2h_ncaaw_cache/summary_*.json` (NCAAW). Cached core season averages:
  `h2h_cache/core_*.json` and `h2h_ncaaw_cache/core_*.json`.
- New core API fetches only for the newly identified champions' season
  averages (NCAAW 2023 LSU, 2024 South Carolina, 2025 UConn, 2026 UCLA;
  NBA 2023 Denver). No other network calls except those.
- No PropLine, Kalshi, or Polymarket keys are used (none are on file).
- No odds, no bets, no moneyline placement. Log-only backtest.

## Deliverables and report

- Script + results JSON in ~/workspace/muse-prop1/, named
  h2h_backtest_champion_*.
- Final report: Part 1 standalone win rates per arm and pooled vs baselines,
  best/worst champion metrics, overlap with H2H votes; Part 2 with-vs-without
  win rates per arm vs baselines; Part 3 power-rank win rates per arm and
  pooled vs baselines, direct comparison to the binary champion vote,
  power-score distribution, and margin-quartile win rates; and a plain
  verdict on whether the champion comparison (binary or power rank) has any
  signal anywhere. If it has no signal anywhere, say so plainly — do not
  rescue it.
- Nothing in the Prop1 engine, runner, or any bot code is modified by this
  test.
