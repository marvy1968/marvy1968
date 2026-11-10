# H2H College Football Backtest — Frozen Spec (2026-11-10)

## The system (same design as the basketball H2H, adapted for football)

Compare the two teams' season averages metric by metric. Whoever wins a
metric gets +1; a tie on a metric is 0 points for both. Higher H2H total =
the moneyline lean / pick. Home field: the home team gets +1. A team with a
starter out loses 1 — EXCLUDED here (see below, no reliable per-game
starter-status source in the ESPN API for college football either).

This is the football twin of `h2h-backtest-spec-2026-11-08.md` (basketball).
Same vote rule, same no-lookahead discipline, same three-arm structure
(regular season this year / postseason last year / full validation season),
same champion comparison in three parts (see § Champion comparison).

## Data source probe (done before freezing)

Per-game team stats come from the ESPN site API game summary
(`.../sports/football/college-football/summary?event={id}`), in
`boxscore.teams[].statistics`. Probed 2026-11-10 on Notre Dame vs Miami
(event 401858291, Nov 7 2026). The fields below are populated for both teams
in that game. Fields that are NOT in the per-game summary are excluded from
the shared metric list (they cannot be accumulated into averages):

- Sacks: present in the core API season stats but NOT as a per-game team
  stat in the summary. Excluded.
- Fourth-down efficiency: present, but 0–2 attempts per game — too noisy to
  vote on. Excluded.
- Completion % / comp-att: present, but redundant with yards per pass.
  Excluded.
- Rushing attempts: present, but a volume stat, not an efficiency/quality
  stat. Excluded.

## Frozen metric list (14 metrics, same list in all three arms)

Team season averages, per game. Direction = which side "wins" the metric.
All 14 are accumulated from the per-game summary stats (plus the score for
points and points allowed).

| # | Metric | Source field (per game) | Higher wins? |
|---|--------|-------------------------|--------------|
| 1 | Points per game | game score | higher |
| 2 | Total yards per game | `totalYards` (displayValue) | higher |
| 3 | Passing yards per game | `netPassingYards` | higher |
| 4 | Rushing yards per game | `rushingYards` | higher |
| 5 | First downs per game | `firstDowns` | higher |
| 6 | Third-down efficiency | `thirdDownEff` (value, fraction) | higher |
| 7 | Yards per pass | `yardsPerPass` | higher |
| 8 | Yards per rush | `yardsPerRushAttempt` | higher |
| 9 | Turnovers per game | `turnovers` (displayValue) | LOWER |
| 10 | Takeaways per game | opponent's `turnovers` | higher |
| 11 | Points allowed per game | opponent's score | LOWER |
| 12 | Yards allowed per game | opponent's `totalYards` | LOWER |
| 13 | Penalty yards per game | `totalPenaltiesYards` (displayValue "5-30" → 30) | LOWER |
| 14 | Time of possession (seconds) | `possessionTime` (value, seconds) | higher |

Derivable metrics (10, 11, 12) come from the opponent's side of the same
game — no extra fetch. `totalYards`, `turnovers`, and
`totalPenaltiesYards` carry their value in `displayValue` only (the numeric
`value` is "-"), so the script parses the display string.

Excluded deliberately: sacks (not in the per-game summary), fourth-down
efficiency (too few attempts), completion % (redundant with yards per pass),
rushing attempts (volume, not quality). Pace has no football equivalent and
is not a metric.

## Vote, home field, pick rule

- Vote: +1 to the team that wins each metric, 0 on a tie.
- Home field: +1 to the home team. Neutral-site games: no home point for
  either team (the scoreboard still labels one team "home"; the neutralSite
  flag governs). Bowl games and the CFP are neutral-site.
- Starter injuries: EXCLUDED from all three arms. There is no reliable
  per-game starter-status source in the ESPN API for college football.
  Nothing is guessed. If starter data becomes available later, it is a new
  test, not a silent addition.
- Pick: higher H2H total = moneyline pick. Tied totals = no pick.
- A game is evaluated only if BOTH teams have pre-game averages. A team with
  no prior game in the sample has no averages, so that game is skipped and
  counted as "no data" (not as a loss).

## Win-rate calculation and baselines

- ML win rate = picks won / picks made (ties in H2H total are not picks).
- Baseline, Arms A and C: home-team win rate over the SAME evaluated games
  (home wins / games evaluated). The H2H system must beat simply picking
  the home team.
- Baseline, Arm B: higher-AP-ranked team win rate over the same evaluated
  games (higher-ranked wins / evaluated games where both teams are ranked
  in the final regular-season AP Top 25; games where one or both teams are
  unranked have no baseline pick and are reported separately).
- Per-metric win rate: for each metric, over evaluated games where that metric
  was decisive (not tied), the fraction where the team that won the metric went
  on to win the game.

## Arm A — CFB this season (2026-27 regular season)

- Sample: completed FBS games Aug 29 – Nov 8, 2026. Scoreboard with
  groups=80 (the FBS group; returns the full FBS slate). ~700 games in the
  sample as of the Nov 8 check.
- Averages: per-game team stats from each game's ESPN summary boxscore,
  accumulated in date order. For a game on date D, each team's averages use
  only that team's games with date strictly before D (no within-day ordering
  issues; a team plays at most once per day).
- Home field: applies (regular-season home/away games; neutral-site games
  get no home point).
- Baseline: home-team win rate.
- Caveat (frozen): early-season averages are noisy — teams have 1–3 prior
  games in Week 1, and Week 0/1 opening games have no prior data at all, so
  no picks can be made on them. The evaluated sample will be far smaller
  than the full slate. Report the evaluated count plainly.

## Arm B — CFB last year's postseason (2025-26 CFP + bowls)

- Sample: the 2025-26 College Football Playoff (12-team format, 11 games)
  plus the 2025-26 bowl games, Dec 2025 – Jan 2026. ~46 games. Identified
  by the postseason flag / bowl-game marker on the scoreboard; all are
  neutral-site.
- Averages: each team's FULL regular-season averages, accumulated from that
  team's per-game summary stats over the entire 2025-26 regular season
  (same 14 metrics, same accumulation method as Arms A and C — NOT the
  core API season stats, because the core API does not carry 7 of the 14
  metrics: first downs, yards per pass, yards per rush, points allowed,
  yards allowed, penalty yards, time of possession). The averages contain
  no postseason games and no lookahead.
- Home field: does NOT apply. All postseason games are neutral-site; no
  team gets the +1 home point.
- Baseline: higher-AP-ranked team win rate (final regular-season AP Top 25,
  both teams ranked; unranked matchups reported separately with no baseline
  pick).
- This is the strongest small-sample test: full-season averages, complete
  postseason sample, no early-season noise. But it is small (~46 games) —
  report the sample size plainly and do not dress it up.

## Arm C — Validation season (full 2025-26 regular season)

- Sample: ALL completed FBS regular-season games, Aug 23 – Nov 29, 2025.
  ~800 games. This is the strongest test — the football equivalent of the
  NBA 2025-26 validation season (1,212 picks) that is the only H2H arm that
  passed.
- Averages: per-game team stats from each game's ESPN summary boxscore,
  accumulated in date order, same strictly-earlier-date rule as Arm A.
- Home field: applies.
- Baseline: home-team win rate.
- Caveat (frozen): Week 0/1 games have no prior data and are skipped. The
  evaluated sample will still be large (most teams play 12 games, so most
  games from Week 2 onward have prior data).

## Champion comparison (three parts — same template as basketball)

Benchmark for each of the 14 metrics = the average across the last 3
champions' season averages on that metric. Champions (from the CFB power
rank spec, identified from ESPN scoreboard championship-game winners):
Michigan 2023, Ohio State 2024, Indiana 2025. The 2026 champion is not
decided yet and is not in any benchmark.

Champion season averages are accumulated from the champions' per-game
summary stats over their full regular seasons (same 14 metrics, same
method as the arms) — NOT the core API, which lacks 7 of the 14 metrics.
A team earns +1 champion vote per metric where its pre-game average
meets/beats the benchmark in the good direction (>= for higher-is-better,
<= for lower-is-better; tying counts as meeting it). Both teams can earn the
vote on the same metric, or neither can. Maximum 14 champion votes per team
per game.

Lookahead rule: the benchmark for a game uses only champions whose seasons
were completed BEFORE that game. Arms A and C (2026-27 and 2025-26
regular seasons): champions from 2023, 2024, 2025 — the 2026 champion had
not been decided yet in either season. Arm B (2025-26 postseason):
champions from 2023, 2024, 2025 as well (the 2025 champion, Indiana, was
decided Jan 19, 2026 — before the postseason games it is benchmarked
against; the 2026 champion was not decided).

- **Part 1 — champion votes as a STANDALONE pick rule.** Pick the team
  with MORE champion votes. Tied champion votes = no pick. No H2H votes,
  no home-field point. Report per arm AND pooled: games, picks, wins,
  ML win rate vs the arm baseline. Per-metric champion win rate (decisive
  games only). Overlap with H2H votes: agreement rate, correlation between
  the champion-vote difference and the H2H-vote difference, and which pick
  was right when they disagreed.
- **Part 2 — champion factor with vs without.** Totals WITH champion =
  H2H votes + champion votes + home point (regular-season arms; no home
  point in the postseason arm). WITHOUT champion = H2H votes + home point
  (the H2H-only totals — recomputation must reproduce them exactly as a
  self-check). Report per arm: picks, wins, ML win rate WITH and WITHOUT,
  vs the baseline.
- **Part 3 — POWER RANK vs the champions.** Same formula as basketball
  Part 3: for each metric, the normalised difference from the benchmark —
  (team − benchmark)/benchmark for higher-is-better, (benchmark − team)/
  benchmark for lower-is-better. Power score = mean of the 14 normalised
  differences. Pick the team with the higher power score; tied within
  0.001 = no pick. No H2H votes, no home-field point. Report per arm AND
  pooled: win rate vs baseline, direct comparison to the binary champion
  vote (does the continuous power rank beat the binary vote?), power-score
  distribution (how many teams are above the champion standard at all),
  and margin-quartile win rates (does a bigger power-score gap predict a
  higher win rate?).

## No-lookahead rules (all arms, all parts)

- Arms A / C: a game's averages use only games played on strictly earlier
  dates. The game being predicted never contributes to its own averages.
- Arm B: averages are the pre-postseason regular season only. No
  postseason game contributes to any average.
- Champion benchmarks use only champions whose seasons were completed
  before the game.
- The pick for a game is computed only from data available before that game.

## Data sources

- ESPN site API (same base Prop1's live_feed uses):
  `https://site.api.espn.com/apis/site/v2/sports/football/college-football`
  — scoreboard by date (groups=80 for the FBS slate); game summary for
  per-game team boxscore stats (`boxscore.teams[].statistics`).
- ESPN core API only for team-ID resolution if needed (not for averages —
  see Arm B above).
- AP Top 25 (final regular-season poll) for the Arm B baseline only, from
  the same poll source as `top30_ncaaf.yaml`.
- No PropLine, Kalshi, or Polymarket keys are used (none are on file).
- No odds, no bets, no moneyline placement. Log-only backtest.

## Deliverables and report

- Script + results JSON in ~/workspace/muse-prop1/, named h2h_backtest_cfb_*.
- Final report: for EACH arm — games in sample, games evaluated, games
  skipped (no data / tied total), picks made, wins, losses, ML win rate,
  baseline win rate, per-metric win rates, and the caveats (sample size,
  early-season noise, no lookahead). All three arms side by side. For the
  champion comparison: Part 1 standalone win rates per arm and pooled vs
  baselines, best/worst champion metrics, overlap with H2H votes; Part 2
  with-vs-without win rates per arm vs baselines; Part 3 power-rank win
  rates per arm and pooled vs baselines, direct comparison to the binary
  champion vote, power-score distribution, and margin-quartile win rates;
  and a plain verdict on whether the champion comparison (binary or power
  rank) has any signal anywhere. If it has no signal anywhere, say so
  plainly — do not rescue it.
- If the validation arm (C) is the stronger test, say so plainly.
- Nothing in the Prop1 engine, runner, or any bot code is modified by this
  test.
