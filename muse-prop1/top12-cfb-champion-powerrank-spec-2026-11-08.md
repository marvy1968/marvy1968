# Top 12 College Football Teams vs the 3-Champion Average — Power Rank
## Frozen spec (2026-11-08)

**Status:** FROZEN before any ranking was computed. The metric list was frozen
after probing the ESPN data source (see §3) and before any team's power score
was calculated. No re-tuning after seeing results.

**Interpretation:** the top 12 teams in the current (2026) college football
AP Top 25, ranked by the champion power score — the same formula as Part 3 of
the basketball champion test (mean across metrics of the normalised difference
from the champion benchmark). This is a COLLEGE FOOTBALL ranking, not men's
college basketball (scope corrected by the user 2026-11-08).

## 1. Top 12 — AP Top 25, Week 11, released Nov 8, 2026

Source: AP Top 25 poll, Nov 8, 2026 (Ball Exclusives full poll; cross-checked
vs On3, Saturday Down South, Reuters/SRN). The local
`~/workspace/muse-prop1/top30_ncaaf.yaml` (refreshed 2026-11-08 from the same
poll) agrees exactly on the top 12 and their order.

| AP rank | Team | Record |
|---|---|---|
| 1 | Georgia | 8-1 |
| 2 | Utah | 9-0 |
| 3 | Notre Dame | 8-1 |
| 4 | UCLA | 9-0 |
| 5 | Miami | 8-1 |
| 6 | Indiana | 8-1 |
| 7 | Alabama | 7-2 |
| 8 | Texas | 7-2 |
| 9 | Tennessee | 7-2 |
| 10 | Texas Tech | 8-1 |
| 11 | Wake Forest | 8-1 |
| 12 | BYU | 7-2 |

## 2. Champions — the last 3 whose seasons finished before now

The 2026 champion is NOT decided yet (season in progress, Week 11), so it is
not in the benchmark. Identities determined from ESPN scoreboard data
(`site.api.espn.com/.../college-football/scoreboard`), championship-game
winners — not prior knowledge:

| Season | Championship game (date) | Result (ESPN scoreboard) | Champion |
|---|---|---|---|
| 2023 | CFP National Championship, Jan 8, 2024 | Michigan 34, Washington 13 | **Michigan** |
| 2024 | CFP National Championship, Jan 20, 2025 | Ohio State 34, Notre Dame 23 | **Ohio State** |
| 2025 | CFP National Championship, Jan 19, 2026 | Indiana 27, Miami 21 | **Indiana** |

Benchmark = the mean of the three champions' season averages, metric by
metric (see §4).

## 3. Data source and season type

- Source: ESPN core API team statistics —
  `sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/{season}/types/2/teams/{team_id}/statistics`
  (same core-API family as the basketball H2H tests).
- Season type `types/2` = regular season **including the conference
  championship game** (verified: Michigan 2023 shows 13 games = 12 + Big Ten
  title game; Ohio State 2024 shows 12; Indiana 2025 shows 13). Used for ALL
  teams — current and champions — so the comparison is like-for-like. Current
  teams are through Week 11 (9 games for 8-1 teams, 10 for 9-0/7-2 teams —
  conference title games not yet played).
- Team IDs resolved from ESPN scoreboard data (Week 10 scoreboard + the three
  championship games): Georgia 61, Utah 254, Notre Dame 87, UCLA 26,
  Miami 2390, Indiana 84, Alabama 333, Texas 251, Tennessee 2633,
  Texas Tech 2641, Wake Forest 154, BYU 252, Michigan 130, Ohio State 194.

## 4. Frozen metric list (8 metrics)

Probed the endpoint before freezing: the fields below are populated
(non-zero) for every one of the 15 teams (12 current + 3 champions).

| # | Metric | ESPN field(s) | Direction |
|---|---|---|---|
| 1 | points per game | `totalPointsPerGame` | higher better |
| 2 | total yards per game | `yardsPerGame` | higher better |
| 3 | rushing yards per game | `rushingYardsPerGame` | higher better |
| 4 | passing yards per game | `passingYardsPerGame` | higher better |
| 5 | turnovers per game | `totalGiveaways` / `gamesPlayed` | **lower better** |
| 6 | takeaways per game | `totalTakeaways` / `gamesPlayed` | higher better |
| 7 | third-down conversion % | `thirdDownConvPct` | higher better |
| 8 | sacks per game | defensive `sacks` / `gamesPlayed` | higher better |

### Dropped metrics (unavailable — do not guess them)

The endpoint returns **0.0 for every team and every season checked**
(current 2026 teams and all three champion seasons), i.e. these fields are
simply not populated in this endpoint:

- points allowed per game (`defensive.pointsAllowed` = 0.0)
- yards allowed per game (`defensive.yardsAllowed` = 0.0)
- red-zone TD % (`miscellaneous.redzoneTouchdownPct` = 0.0)

They are dropped from the list per the task rule. Consequence, stated
plainly: **this ranking has no points-allowed / yards-allowed component —
it is an offensive + ball-security + one defensive-disruption (sacks) power
rank, not a complete team rating.** A team with a great defense but an
average offense will rank lower here than it would on a full rating.

## 5. Power score (same formula as basketball Part 3)

For each metric, the normalised difference from the champion benchmark:

- higher-is-better: `(team − benchmark) / benchmark`
- lower-is-better (turnovers per game only): `(benchmark − team) / benchmark`

**Power score = mean of the 8 normalised differences.** Positive = above
champion level on average; negative = below it.

Champion-vote count = number of the 8 metrics on which the team meets/beats
the benchmark in the good direction (≥ for higher-better, ≤ for
lower-better), same as the basketball champion-vote rule.

## 6. Ranking and reporting

- Rank the 12 teams by power score, highest first.
- Per team: rank, team, power score, champion-vote count (of 8).
- Report how many of the 12 are above the champion standard (power score > 0).
- No odds, no bets, no keys. Local only, nothing pushed to GitHub.

## 7. Caveats

- Current teams' averages are through Week 11 (~9–10 games) — far more
  stable than the basketball 1–3-game averages, but still a snapshot: the
  ranking will move as the rest of the season (including conference title
  games) is played.
- The benchmark is the mean of 3 champions, not a distribution: a team can
  be above the benchmark on a metric while still being below the best
  champion on it.
- No defensive points/yards component (see §4) — do not present this as a
  complete team rating.
- Rushing + passing yards are components of total yards, so offense is
  weighted more heavily than defense by design (same overlapping-metric
  approach as the basketball test, which had rebounds + off_reb + def_reb).
