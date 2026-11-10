# H2H Basketball Backtest — Frozen Spec (2026-11-08)

## The system (user's, as specified in chat 2026-11-08)

Compare the two teams' season averages metric by metric. Whoever wins a metric
gets +1; a tie on a metric is 0 points for both. Higher H2H total = the
moneyline lean / pick. Home court: the home team gets +1. A team with a starter
out loses 1. Rivalry is NOT a factor (user considered it and rejected it).

User's example: GA vs Bama basketball — points avg 100 vs 102 = Bama +1,
offensive rebounds 19 vs 18 = GA +1, totals Bama 20 vs GA 19, so Bama favored.

## Frozen metric list (14 metrics, same list in all three arms)

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
season stats used for Arm B, so it cannot be in the shared list); pace
(a style, not a quality metric — faster is not better, so it cannot be voted on).

Assist/turnover ratio: assists / max(turnovers, 0.1) computed from the averaged
assists and averaged turnovers, so a zero-turnover average cannot divide by zero.

## Vote, home court, starter injuries, pick rule

- Vote: +1 to the team that wins each metric, 0 on a tie.
- Home court: +1 to the home team. Neutral-site games: no home point for
  either team (the scoreboard still labels one team "home"; the neutralSite
  flag governs).
- Starter injuries: EXCLUDED from all three arms. The ESPN injuries endpoint
  returns an empty list for NCAAB, and there is no reliable per-game
  starter-status source in the ESPN API. Nothing is guessed. If starter data
  becomes available later, it is a new test, not a silent addition.
- Pick: higher H2H total = moneyline pick. Tied totals = no pick.
- A game is evaluated only if BOTH teams have pre-game averages. A team with
  no prior game in the sample has no averages, so that game is skipped and
  counted as "no data" (not as a loss).

## Champion reference (added 2026-11-08, user: "the champion reference")

For each of the 14 frozen metrics, the benchmark is the average across the
last 3 champions' season averages on that metric. A team earns +1 per metric
where its pre-game average meets/beats the benchmark in the good direction.
"Meets/beats" means >= the benchmark for higher-is-better metrics and <= the
benchmark for lower-is-better metrics; tying the benchmark counts as meeting
it (+1). Both teams can earn the champion point on the same metric, or
neither can.

Direction per metric (frozen):
- Higher-is-better: points, rebounds, offensive rebounds, defensive rebounds,
  assists, steals, blocks, threes made, FG%, 3P%, FT%, assist/turnover ratio.
- Lower-is-better: turnovers, fouls.
- Opponent points per game is NOT in the frozen metric list (it is not
  available in the core-API season stats used for Arm B), so it has no
  champion vote either. The user's direction list mentioned it; it is excluded
  here for the same data-availability reason as in the base metric list.

Champion votes are ADDED to the H2H total. The total per game is:
H2H metric votes + champion votes + home court (+1, Arms A and C only) +
injuries (0, excluded). This double-counts each metric by design — a team can
earn up to 2 points per metric (1 for beating the opponent, 1 for beating the
champion benchmark). The report must show that plainly, and must report the
components separately per game and per arm (champion votes vs H2H votes vs
home court vs injuries), plus the pick/win rate WITH the champion factor and
WITHOUT it (H2H-only), so the champion factor's contribution is visible.

Champion sets (lookahead rule — the benchmark for a game uses only champions
whose seasons were completed BEFORE that game):
- Arm A (NCAAB 2026-27) and Arm C (NBA 2026-27): champions from 2024, 2025,
  and 2026.
- Arm B (NCAAB postseason, March 2026): champions from 2023, 2024, and 2025
  (the 2026 champion had not been decided yet).
- NCAAB champions = NCAA Tournament champions; NBA champions = NBA Finals
  champions. Identified from ESPN scoreboard data (championship-game winners
  / last Finals-game winners), not from prior knowledge.
- Champion identities, from ESPN data: NCAAB 2023 UConn (id 41), 2024 UConn
  (id 41), 2025 Florida (id 57), 2026 Michigan (id 130). NBA 2024 Boston
  Celtics (id 2), 2025 Oklahoma City Thunder (id 25), 2026 New York Knicks
  (id 18). UConn won in both 2023 and 2024, so both of its season averages
  enter the Arm B benchmark — that is correct per the "last 3 champions" rule.
- Champion season averages: ESPN core API, season type 2 (regular season),
  for the championship season. NCAAB league path
  mens-college-basketball; NBA league path nba. Per-game averages are taken
  from the core API's avg* stats where present, otherwise computed as
  total / gamesPlayed. The benchmark per metric is the mean of the 3
  champions' season averages.

## Win-rate calculation and baselines

- ML win rate = picks won / picks made (ties in H2H total are not picks).
- Baseline, Arms A and C: home-team win rate over the SAME evaluated games
  (home wins / games evaluated). The H2H system must beat simply picking the
  home team.
- Baseline, Arm B: higher-seed win rate over the same evaluated games
  (higher-seed wins / evaluated games where the seeds differ; tied-seed games
  have no baseline pick and are reported separately).
- Per-metric win rate: for each metric, over evaluated games where that metric
  was decisive (not tied), the fraction where the team that won the metric went
  on to win the game.

## Arm A — NCAAB this season (2026-27)

- Sample: completed NCAAB games Nov 2–8, 2026. Scoreboard with groups=50
  (the default scoreboard returns only a small featured subset; groups=50
  returns the full Division I slate). 445 completed games as of the Nov 8
  check (Nov 8 itself had 36 events, 10 final at check time — the script
  fetches at run time and reports the actual count).
- Averages: per-game team stats from each game's ESPN summary boxscore,
  accumulated in date order. For a game on date D, each team's averages use
  only that team's games with date strictly before D (no within-day ordering
  issues; a team plays at most once per day).
- Home court: applies (regular-season home/away games; neutral-site games get
  no home point).
- Baseline: home-team win rate.
- Caveat (frozen): early-season averages are noisy — teams have 1–4 prior
  games, and Nov 2 (opening day, 182 games) has no prior data at all, so no
  picks can be made on Nov 2. The evaluated sample will be far smaller than
  445. Report the evaluated count plainly.

## Arm B — NCAAB last year's postseason (2026 NCAA Tournament)

- Sample: the 2026 NCAA Men's Basketball Tournament, 67 games, March 17 –
  April 6, 2026. Identified by the frozen filter: neutralSite = True AND both
  teams' curatedRank in 1..16 (curatedRank is the tournament seed). Verified
  in exploration: this filter returns exactly 67 games; the other postseason
  games on those dates are NIT (curatedRank 99, mostly non-neutral) and are
  excluded.
- Averages: each team's FULL regular-season averages from the ESPN core API,
  season 2026, season type 2 (regular season), one call per team (68 teams).
  Type 2 excludes the postseason, so the averages contain no tournament games
  and no lookahead. Same 14 metrics, taken from the core API's per-game
  averages (avgPoints, avgRebounds, avgOffensiveRebounds,
  avgDefensiveRebounds, avgAssists, avgSteals, avgBlocks, avgTurnovers,
  avgFouls, avgThreePointFieldGoalsMade, fieldGoalPct,
  threePointFieldGoalPct, freeThrowPct, assistTurnoverRatio).
- Home court: does NOT apply. All tournament games are neutral-site; no team
  gets the +1 home point.
- Baseline: higher-seed (lower curatedRank number) win rate over the same
  evaluated games.
- This is the strongest test: full-season averages, complete 67-game sample,
  no early-season noise.

## Arm C — NBA this season (2026-27)

- Sample: completed NBA games Oct 20 – Nov 8, 2026. Scoreboard on the
  basketball/nba path, DEFAULT (no groups parameter — groups=50 returns zero
  events for the NBA). 139 completed games as of the Nov 8 check (Nov 8 had
  7 events, 0 final at check time — the script fetches at run time and reports
  the actual count).
- Averages: per-game team stats from each game's ESPN summary boxscore,
  accumulated in date order, same strictly-earlier-date rule as Arm A.
- Home court: applies (NBA regular-season games are home/away; neutral-site
  games get no home point).
- Baseline: home-team win rate.
- Caveat (frozen): the season started in late October, so teams have roughly
  8–12 games each — averages are more stable than Arm A's, but still early
  season. Opening-night games have no prior data and are skipped.

## Data sources

- ESPN site API (same base Prop1's live_feed uses):
  `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball`
  and `.../basketball/nba`. Scoreboard by date; game summary for per-game
  team boxscore stats.
- ESPN core API for Arm B season averages:
  `https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/seasons/2026/types/2/teams/{id}/statistics?lang=en&region=us`
- No PropLine, Kalshi, or Polymarket keys are used (none are on file).
- No odds, no bets, no moneyline placement. Log-only backtest.

## No-lookahead rules (all arms)

- Arm A / Arm C: a game's averages use only games played on strictly earlier
  dates. The game being predicted never contributes to its own averages.
- Arm B: averages are the pre-tournament regular season only (core API
  type 2). No tournament game contributes to any average.
- The pick for a game is computed only from data available before that game.

## Deliverables and report

- Script + results JSON in ~/workspace/muse-prop1/, named h2h_backtest_*.
- Final report: for EACH arm — games in sample, games evaluated, games
  skipped (no data / tied total), picks made, wins, losses, ML win rate,
  baseline win rate, per-metric win rates, and the caveats (sample size,
  early-season noise, starter injuries excluded, no lookahead). All three arms
  side by side. For the champion factor, report per arm: champion votes
  (total and per-team average), H2H votes, home-court points, and the pick/win
  rate WITH the champion factor and WITHOUT it (H2H-only), so the champion
  factor's contribution is visible — including that it double-counts each
  metric by design. If a sample is too small to trust, say so — do not dress
  up a tiny sample as a result.
- If the postseason arm (B) is the stronger test, say so plainly.
- Nothing in the Prop1 engine, runner, or any bot code is modified by this test.
