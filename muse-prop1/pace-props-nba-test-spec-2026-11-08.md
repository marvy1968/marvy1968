# In-game pace for team and player props — NBA — frozen spec (2026-11-08)

**Status:** FROZEN before any test code or results. User approved the NBA run 2026-11-08.
**Note 2026-11-10 (does not change the frozen test):** `bovada.py` was removed
per Grok's review and replaced by `propline.py`; `extract_bovada_lines` is now
`extract_propline_lines`. References to the old names below are historical.
**Scope:** Muse Prop1, basketball, NBA only. Same design as the college pace test
(`pace-props-test-spec-2026-11-08.md`), fresh frozen spec. No bets, no odds, no
keys. Log-only. Local only — do not push to GitHub. Do NOT touch the college
code paths or results; NBA support is additive.

## Background (from the college test, 2026-11-08)

- The college pace test was **REJECTED** as specified: team props won at all
  checkpoints and pooled (team points MAE 8.18 vs 9.28 naive), player props
  lost at all checkpoints and pooled (player points 2.89 vs 2.85 naive).
  The working explanation is double-counting: a player's live per-minute
  rate already reflects the game's pace, so multiplying by the pace ratio
  counts pace twice.
- The engine now measures real live pace (Dean Oliver possessions from the
  ESPN boxscore) and has team projection (`project_team_total`) — reused here.

## Feed verification (done BEFORE this spec — measurements, not results)

Fetched 2026-11-08 ~18:00 CST from the ESPN site API (free/keyless):
scoreboards Oct 20–Nov 8, 2026 → completed regular-season (type 2) games →
summary per game, saved to `h2h_nba_cache/summary_<id>.json`. **140 games,
140 fetched, 0 failed.** Game date = `header.competitions[0].date` (UTC),
same convention as the college test. Nov 3 had no US games (Election Day),
so there are no Nov 4 UTC games. The cache is fixed as fetched.

### NBA differences from college (all frozen here)

- **48-minute game, 4×12-minute quarters** (college: 40 min). Elapsed for a
  play: `elapsed = (period-1)*12 + (12 - clock_remaining)`.
- **Clock format:** NBA clocks are `"M:SS"` normally but `"SS.S"` (seconds
  with tenths, no colon) under a minute — e.g. `"57.9"`, `"4.4"`, `"0.2"`.
  The college clock parser (`(\d+):(\d+)` only) returns None for those and
  the replay defaulted elapsed to 48.0, which broke minutes reconstruction
  (median |minutes diff| up to 23 min). The NBA parser handles both formats.
- **Heaves are not FGAs in the NBA.** The boxscore does not count desperation
  heaves as field-goal attempts, but the feed marks them `shootingPlay` with
  `pointsAttempted` 2/3. Excluding plays with `"Heave"` in `type.text`:
  FGA exact on 269/280 teams (vs 166/280 without the exclusion).
  129 heave plays in the 140 games.
- **NBA boxscore rebounds are player-only.** The boxscore `totalRebounds` /
  `offensiveRebounds` do NOT include team rebounds, unlike college. The
  plays array has many `"... team rebound"` plays with `participants: null`.
  Counting only rebound plays WITH a participant: team rebounds exact on
  272/280 teams, OREB exact on 275/280 (all non-exact within 2, inside the
  gate tolerance). Counting all rebound plays overcounts by ~11–17 per team.
- **Substitutions are one play, not two.** NBA text is `"X enters the game
  for Y"` — a single play per substitution, `participants[0]` is the
  ENTERING player (verified on a 713-play sample, 0 mismatches), the exiting
  player Y is named in the text and mapped to an id via the boxscore roster
  `displayName` (0 misses in 9,821 sub plays across all 140 games).
  The college `"subbing in" / "subbing out"` text never appears in NBA feeds.
- **Possession agreement (Dean Oliver, frozen formula):** boxscore-based mean
  |poss_home − poss_away| = **2.49** over the 107 priors games (college:
  1.70 NCAAB / 1.52 NCAAW). Replay-based (player-only OREB rule): **2.56**
  over 132 regulation games, vs 3.03 for the all-OREB rule — the player-only
  rule wins, so it is frozen. In relative terms 2.56 / ~102 poss ≈ 2.5%,
  vs college 1.70 / 70 ≈ 2.4% — comparable.
- **Season pace (derived from the priors data, NOT assumed):** mean game
  possessions over the 107 priors games (Oct 20–Nov 3) = **102.41**, so
  **SEASON_PACE = 2.13 poss/min** (measured 2.1334; frozen at 2.13).
  Pregame per-possession rate is derived by the engine as
  `pregame_mean / (total_min × season_poss_per_min)` = pregame_mean / 102.2.
- **SD inflation:** frozen at **1.25** (the NCAAB value, the basketball
  default). It only scales `sd_live`, which this MAE test does not use —
  the test scores the projection mean only — so it cannot affect the
  verdict. Kept at the default so the test isolates pace. Not tuned after
  results.

### Reconstruction rules (NBA, frozen)

Played in sequence order (verified monotonic, 0 backwards jumps in the
sampled game). A play counts toward a checkpoint if its elapsed ≤ checkpoint.

- **FGA:** `shootingPlay` and `pointsAttempted` in (2, 3), EXCLUDING
  `"Heave"` in `type.text`.
- **FTA:** `shootingPlay` and `pointsAttempted` == 1.
- **OREB (for possessions):** `type.text == "Offensive Rebound"` WITH a
  participant (player rebounds only; null-participant team rebounds excluded).
- **Team rebounds (for the team total):** plays with `"Rebound"` in
  `type.text`, EXCLUDING `"Dead Ball Rebound"`, WITH a participant.
- **Player rebounds:** `"Rebound"` in type, excluding `"Dead Ball Rebound"`,
  with a participant; the participant is credited.
- **Assists:** scoring play with ≥2 participants → `participants[1]` is the
  assister (credit player + team). Exact on 278/280 teams. KNOWN GAP (same
  as college): ~0–1 assist per team per game is credited in the boxscore
  but tagged on no play; accepted as reconstruction noise.
- **Turnovers:** `"Turnover"` in `type.text`. Do NOT add Steal plays.
- **Threes made:** `scoringPlay` and `pointsAttempted` == 3.
- **Team points at a checkpoint:** `awayScore`/`homeScore` on the last
  included play (scoring plays by team id).
- **Player points:** `scoreValue` on scoring plays, credited to
  `participants[0]`.
- **Player minutes:** starters on court from 0:00; `"X enters the game for
  Y"` plays toggle on/off (entering player = `participants[0]`, exiting
  player = name after `"for"`, mapped via boxscore `displayName`).

**Reconstruction validation gate (per game, same as college):** the full-time
reconstruction must match the boxscore exactly on points, FGA, FTA, threes,
turnovers, and within 2 on rebounds / OREB / assists; and the median
|reconstructed MIN − boxscore MIN| across players must be ≤ 1.0 min.
A game failing any of these is EXCLUDED from the test and counted as a
reconstruction failure (reported, not silently dropped).

**Validation result (pre-spec, all 140 games):** pass **122**, fail **10**,
OT 8, no-plays 0. All 10 failures are FGA ±1 (tip/putback feed noise —
scattered cases, no systematic rule found after inspection; e.g. a tip the
feed records but the boxscore does not count, or vice versa). Minutes pass
everywhere after the clock + substitution fixes. No turnover / points /
threes / FTA failures anywhere.

## Engine changes (additive only — frozen here, applied after this spec)

- `engine.py` `SPORTS["nba"]`: `total_min` 48, `min_elapsed` 8,
  `sd_inflation` 1.25, `propline_key` `"basketball_nba"` (**NOT verified** —
  verify via /v1/sports before use), `espn_path` `"basketball/nba"`.
  `project_basketball_final` docstring updated (NCAAB / NCAAW / NBA);
  `TeamPaceInput` / `BasketballPaceInput` "40 min total" comments updated
  to note `total_min` is a parameter (48 for NBA). **No formula changes.**
  The engine functions are already parameterised (`total_min`,
  `season_poss_per_min`); the test passes 48 / 2.13.
- `live_feed.py`: `ESPN_PATHS["nba"]`, `PERIOD_MIN["nba"] = 12`,
  `parse_summary` basketball branches include `"nba"`,
  `_clock_to_elapsed` handles the `"SS.S"` clock format (colon regex first,
  so college clocks are unaffected).
- `config.yaml` UNTOUCHED — the runner will not poll NBA until config adds
  it. `bovada.py` UNTOUCHED — no PropLine NBA wiring (key unverified, team
  / player line payload shapes unverified).

## PART 2 — TEST (projection accuracy only)

### Sample (frozen)

- Completed NBA regular-season games, **Oct 20–Nov 8, 2026**, from
  `h2h_nba_cache/summary_*.json` (140 games, fetched as above). No fresh
  fetches for the test run — the cache is fixed.
- **Test window:** games dated **Nov 4–8** only (header/UTC date):
  Nov 4: 0, Nov 5: 14, Nov 6: 2, Nov 7: 10, Nov 8: 7 → **33 games**.
  Games dated Oct 20–Nov 3 are priors only: **107 games**.
  Per-date counts (header date): Oct 20: 2, Oct 21: 6, Oct 22: 7, Oct 23: 7,
  Oct 24: 9, Oct 25: 11, Oct 26: 5, Oct 27: 6, Oct 28: 8, Oct 29: 7,
  Oct 30: 8, Oct 31: 8, Nov 1: 6, Nov 2: 2, Nov 3: 15, Nov 5: 14, Nov 6: 2,
  Nov 7: 10, Nov 8: 7.
- **Exclusions (frozen):** games that went to overtime (max period > 4) —
  projections target a 48-minute final and OT finals would inflate the
  error; games with an empty/missing `plays` array; games failing the
  reconstruction validation gate above (counted separately).
- A team is included for a game only if it has ≥1 prior game in the cached
  set; a player is included only if they have ≥1 prior game in the cached
  set AND `minutes_played > 0` at that checkpoint.

### Pregame priors (no lookahead — strictly earlier dates only)

Computed from the cached set, in chronological order, per team / per player.
Same-date games never feed each other (all games of a date are evaluated
before their finals are recorded as priors).

- Team pregame mean for a market = mean of the team's final totals in its
  prior cached games. Team pregame SD = sample SD of those finals if ≥2
  prior games, else `0.30 × mean`; SD floored at `0.10 × mean`.
- Player pregame mean = mean of the player's per-game stat over prior
  cached games in which the player appeared (final boxscore minutes > 0).
  `pregame_per_min` = pregame mean / mean minutes in those games.
  `pregame_minutes` = mean minutes in those games. Player pregame SD =
  same rule as teams.
- Season pace: **2.13 poss/min** (frozen above). `total_min` 48.

### Arms (per checkpoint, per team / per qualifying player)

Checkpoints: **8, 20, 30 minutes elapsed.** Engine functions used as-is
(`project_basketball_final`, `project_team_total`) with `total_min=48`,
`season_poss_per_min=2.13`; fouls=0, lead=0.0 (foul/blowout rules not
exercised — same as the college test, so the test isolates pace).

- **(a) pace-adjusted:** player — `project_basketball_final` with real
  `poss_so_far` (game possessions at the checkpoint); team —
  `project_team_total` with real live pace and real per-poss rates.
- **(b) pace-naive (current behaviour):** player — `project_basketball_final`
  with `poss_so_far = elapsed × 2.13` (pace ratio exactly 1.0); team —
  `project_team_total` with live pace = season pace and live per-poss rate =
  pregame rate (i.e. projection = current + remaining × season rate).
- **(c) pregame:** `pregame_mean`, constant across checkpoints.

Markets: team points / rebounds / assists / threes (both teams per game);
player points / rebounds / assists / threes (qualifying players only).

### Metric and pass bar (frozen before running — identical to college)

- **Metric:** mean absolute error (MAE) vs the actual final (boxscore final
  totals, regulation games only).
- **PASS BAR:** pace-adjusted (a) beats pace-naive (b) on **pooled MAE at a
  majority of checkpoints (≥2 of 3) AND pooled across checkpoints**, for
  **team points AND player points**. Otherwise the pace feature is REJECTED
  for the NBA as specified — no re-tuning on this data (no cap changes, no
  different shrinkage, no different possession formula on this data).
- Report every variant and checkpoint, including the ones that lose:
  MAE tables per checkpoint × market for all three arms, with n (number of
  projections) per cell; pooled numbers; reconstruction-failure count;
  sample sizes (games, teams, players). Also report whether the NBA result
  agrees with or contradicts the college result, and whether the player-side
  double-counting explanation holds in the NBA too.

### What this test does NOT do

- **No edge vs any betting line.** There are no odds snapshots and no
  PropLine key. This is a projection-accuracy test only. A pass here means
  the pace input improves projections — it does NOT mean the picks would
  beat a line, and must not be presented that way.

## Deliverables

- This spec (`pace-props-nba-test-spec-2026-11-08.md`)
- Additive engine/live_feed changes above (SPORTS entry, NBA period/clock
  handling, docstring/comment updates) + existing tests still passing
- `pace_props_test_nba.py` — replay script (NBA replay/validation adapted
  from `pace_props_test.py`; college script untouched)
- `pace_props_test_nba_results.json` — full MAE tables + sample counts +
  pass/fail verdict
- Final report: frozen NBA constants, possession-reconstruction validation
  number, MAE tables per checkpoint/market for all three projections,
  whether the pass bar was met, plain verdict, and the college comparison.
