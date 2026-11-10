# In-game pace for team and player props — frozen spec (2026-11-08)

**Status:** FROZEN before any code or results. User approved the build + test 2026-11-08.
**Note 2026-11-10 (does not change the frozen test):** `bovada.py` was removed
per Grok's review and replaced by `propline.py`; `extract_bovada_lines` is now
`extract_propline_lines`. References to the old names below are historical.
**Scope:** Muse Prop1, basketball only (NCAAB, NCAAW). No football-adapter changes.
No bets, no odds, no keys. Log-only. Local only — do not push to GitHub.

## Background (verified 2026-11-08, before this spec)

- `engine.py` already has a pace factor for player props: `pace_ratio =
  live_poss_per_min / season_poss_per_min`, capped 0.85–1.15, multiplying the
  player's per-minute rate in `project_basketball_final`.
- But `runner.py` sets `poss_so_far = int(elapsed * 1.75)`, so live pace
  always equals season pace (1.75) and the ratio is always exactly 1.0.
  **The pace adjustment is a no-op today.**
- `bovada.py` MARKETS has only 8 player markets per basketball league.
  There are no team props anywhere in the code.

## Feed verification (done before this spec — what ESPN actually exposes)

- The ESPN site-API summary (`/summary?event=ID`) returns a `plays` array
  (typically ~500 plays/game) with structured fields per play: `period.number`,
  `clock.displayValue`, `type.text`, `team.id`, `participants[].athlete.id`,
  `scoringPlay`, `scoreValue`, `shootingPlay`, `pointsAttempted`,
  `awayScore`/`homeScore` after the play.
- The summary boxscore team block exposes `fieldGoalsMade-fieldGoalsAttempted`,
  `freeThrowsMade-freeThrowsAttempted`, `threePointFieldGoalsMade-...`,
  `totalRebounds`, `offensiveRebounds`, `defensiveRebounds`, `assists`,
  `steals`, `blocks`, `turnovers`, `fouls`. Player entries carry a `starter`
  flag and MIN.
- Substitution plays are structured: one participant each, text
  `"X subbing in/out for Team"`.
- The standalone play-by-play endpoint returns `{}` for these games — the
  summary's own `plays` array is the source. No other endpoint is needed.

### Reconstruction rules (validated against the final boxscore on 5 college
### games before this spec; these rules are FROZEN)

Played in sequence order. Elapsed for a play: NCAAB halves 20 min,
`elapsed = (period-1)*20 + (20 - clock_remaining)`; NCAAW quarters 10 min,
`elapsed = (period-1)*10 + (10 - clock_remaining)`. A play counts toward a
checkpoint if its elapsed ≤ checkpoint.

- **FGA:** `shootingPlay` and `pointsAttempted` in (2, 3).
- **FTA:** `shootingPlay` and `pointsAttempted` == 1.
- **OREB (for possessions):** `type.text == "Offensive Rebound"` (any
  participant status; dead-ball rebounds are a separate type and never
  count). Validated: this rule gives the closest cross-team possession
  agreement (mean |poss_home − poss_away| 1.70 NCAAB / 1.52 NCAAW over 40
  games each, vs 2.63 / 2.27 for participant-only OREB).
- **Team rebounds (for the team total):** plays with `"Rebound"` in
  `type.text`, EXCLUDING `"Dead Ball Rebound"`. (College boxscore team
  totals include team offensive/defensive rebounds but not dead-ball ones.)
- **Player rebounds:** `"Rebound"` in type, excluding `"Dead Ball Rebound"`,
  with a participant; the participant is credited.
- **Assists:** scoring play with ≥2 participants → `participants[1]` is the
  assister (credit player + team). KNOWN GAP: ~0–1 assist per team per game
  is credited in the boxscore but tagged on no play in the feed; no
  text-parsing workaround — accepted as reconstruction noise.
- **Turnovers:** `"Turnover"` in `type.text`. Do NOT add Steal plays
  (they are separate events for the other team; adding them overcounts).
- **Threes made:** `scoringPlay` and `pointsAttempted` == 3.
- **Team points at a checkpoint:** `awayScore`/`homeScore` on the last
  included play.
- **Player points:** `scoreValue` on scoring plays, credited to
  `participants[0]`.
- **Player minutes:** starters on court from 0:00; substitution plays toggle
  on/off; minutes at a checkpoint = sum of on-court intervals up to it.

**Reconstruction validation gate (per game, before the test):** the full-time
reconstruction must match the boxscore exactly on points, FGA, FTA, threes,
turnovers, and within 2 on rebounds / OREB / assists; and the median
|reconstructed MIN − boxscore MIN| across players must be ≤ 1.0 min.
A game failing any of these is EXCLUDED from the test and counted as a
reconstruction failure (reported, not silently dropped).

## PART 1 — BUILD

### 1. Real live pace

**Possession formula (Dean Oliver, frozen):**

```
poss_team = FGA − OREB + TOV + 0.44 × FTA
poss_game = (poss_home + poss_away) / 2
```

- `live_feed.parse_summary` parses the boxscore team block into
  `team_totals: {team_id: {points, fga, fta, oreb, tov, rebounds, assists,
  threes}}` and `possessions: {"home": x, "away": y, "game": z}` (game = mean
  of the two teams). It also returns `home_team_id` / `away_team_id`.
  If the team block is missing/unparseable, `possessions` is None.
- The formula lives once in `engine.py` as `team_possessions(fga, oreb, tov,
  fta) -> float`; `live_feed` imports it.
- `runner.evaluate_line` (basketball branch) passes
  `poss_so_far = int(round(game["possessions"]["game"]))`. If `possessions`
  is None, it logs skip `"no_team_possessions"` and returns None
  (fail-closed — never falls back to the elapsed×1.75 estimate).
- This is a SINGLE-CHANGE ablation for player props: the only change is the
  input (real pace vs assumed pace). The projection code path, caps
  (0.85–1.15), gates, and priors are untouched.

### 2. Team props

New in `engine.py`:

```python
@dataclass
class TeamPaceInput:
    current: float            # team total so far (points, rebounds, assists, threes)
    elapsed_min: float
    total_min: float = 40.0
    pregame_mean: float = 0.0      # expected final team total (season average)
    pregame_sd: float = 0.0        # SD of the team's final totals
    poss_so_far: float = 0.0       # THIS team's possessions so far
    game_poss_per_min_live: float = 0.0  # live game pace (poss/min, mean of both teams)
    season_poss_per_min: float = 1.75
    sd_inflation: float = 1.25     # 1.25 NCAAB / 1.35 NCAAW (SPORTS registry)
```

**Formula (frozen):**

```
w = min(0.85, max(0.10, elapsed_min / total_min))        # same shrinkage as engine
live_pace = min(max(game_poss_per_min_live, 1.75*0.85), 1.75*1.15)   # same 0.85–1.15 cap
blended_pace = w*live_pace + (1-w)*1.75
remaining_poss = blended_pace * (total_min - elapsed_min)

pregame_per_poss = pregame_mean / (40 * 1.75)            # = pregame_mean / 70
if poss_so_far > 0:
    live_per_poss = min(max(current / poss_so_far, pregame_per_poss*0.5),
                        pregame_per_poss*1.5)             # same 0.5–1.5 cap as engine
else:
    live_per_poss = pregame_per_poss
per_poss = w*live_per_poss + (1-w)*pregame_per_poss

projected = current + remaining_poss * per_poss
sd_live = pregame_sd * sqrt(max(0.05, remaining_min/total_min)) * sd_inflation
sd_live = max(sd_live, pregame_sd * 0.35)
```

No foul/blowout adjustments — team totals don't sit. Returns
`(projected_final, sd_live)`.

**Pick gate:** `evaluate_team_pick(projected, sd, line, over_price,
under_price, low_count=False)` in `engine.py` — Normal(mean=proj, sd=sd) for
points/rebounds/assists, Poisson(lambda=proj) for threes (low-count, same as
the runner does for `player_threes`); evaluates both sides through the same
`passes_gates` (7pp min edge, 5% min EV). Returns the better side's
model_p/fair_p/ev/ok/why.

**PropLine wiring — NOT done in this change:** team market keys
(`team_points`, `team_rebounds`, `team_assists`, `team_threes`) are recorded
in `bovada.py` as `TEAM_MARKETS` (documented, not wired). They are NOT added
to `MARKETS` — `extract_bovada_lines` is player-centric (it requires a
`description` player name on outcomes) and the PropLine team-line payload
shape is unverified (no key on file). Live team-prop evaluation waits on a
real PropLine team-lines response. Nothing here guesses that shape.

### 3. Tests (must pass before the Part 2 run)

- Existing: `test_engine.py` (9 tests) and `test_muse_prop1.py` (7 tests)
  must still pass, unmodified in expectation.
- New engine tests: `team_possessions` formula on a known boxscore line;
  `project_team_total` — projection above current when the team is hot,
  faster live pace raises the projection vs season pace, cap respected
  (pace ratio never moves the projection beyond the 1.15 cap's effect);
  `evaluate_team_pick` gates (clear edge fires, ~50/50 line doesn't).
- New integration tests: `parse_summary` on a synthetic basketball summary
  returns the right `team_totals` and `possessions` (game = mean of the two
  teams); `evaluate_line` basketball branch uses real possessions when
  present and returns None + logs `no_team_possessions` when absent
  (fail-closed).

## PART 2 — TEST (projection accuracy only)

### Sample (frozen)

- Completed NCAAB + NCAAW games, **Nov 2–8, 2026**, from the cached ESPN
  summaries already on disk: `h2h_cache/summary_*.json` (456 NCAAB) and
  `h2h_ncaaw_cache/summary_*.json` (472 NCAAW). No fresh fetches unless a
  cached file is corrupt (then fetch that one game from ESPN, free/keyless,
  and note it).
- **Exclusions (frozen):** games that went to overtime (max period > 2
  NCAAB, > 4 NCAAW) — projections target a 40-minute final and OT finals
  would inflate the error; games with an empty/missing `plays` array; games
  failing the reconstruction validation gate above (counted separately).
- **Test window:** games dated **Nov 4–8** only. Games dated Nov 2–3 are
  priors only. A team is included for a game only if it has ≥1 prior game
  in the cached set; a player is included only if they have ≥1 prior game
  in the cached set AND `minutes_played > 0` at that checkpoint.

### Pregame priors (no lookahead — strictly earlier dates only)

Computed from the cached set, in chronological order, per team / per player:

- Team pregame mean for a market = mean of the team's final totals in its
  prior cached games. Team pregame SD = sample SD of those finals if ≥2
  prior games, else `0.30 × mean`; SD floored at `0.10 × mean`.
- Player pregame mean = mean of the player's per-game stat over prior
  cached games in which the player appeared (final boxscore minutes > 0).
  `pregame_per_min` = pregame mean / mean minutes in those games.
  `pregame_minutes` = mean minutes in those games. Player pregame SD =
  same rule as teams.
- Season pace for both leagues: 1.75 poss/min (70 poss/game), matching the
  engine default.

### Arms (per checkpoint, per team / per qualifying player)

Checkpoints: **8, 20, 30 minutes elapsed.**

- **(a) pace-adjusted:** player — `project_basketball_final` with real
  `poss_so_far` (game possessions at the checkpoint); team —
  `project_team_total` with real live pace and real per-poss rates.
- **(b) pace-naive (current behaviour):** player — `project_basketball_final`
  with `poss_so_far = elapsed × 1.75` (pace ratio exactly 1.0); team —
  `project_team_total` with live pace = season pace and live per-poss rate =
  pregame rate (i.e. projection = current + remaining × season rate).
- **(c) pregame:** `pregame_mean`, constant across checkpoints.

Markets: team points / rebounds / assists / threes (both teams per game);
player points / rebounds / assists / threes (qualifying players only).

### Metric and pass bar (frozen before running)

- **Metric:** mean absolute error (MAE) vs the actual final (boxscore final
  totals, regulation games only).
- **PASS BAR:** pace-adjusted (a) beats pace-naive (b) on **pooled MAE at a
  majority of checkpoints (≥2 of 3) AND pooled across checkpoints**, for
  **team points AND player points**. Otherwise the pace feature is REJECTED
  as specified — no re-tuning on this data (no cap changes, no different
  shrinkage, no different possession formula on this data).
- Report every variant and checkpoint, including the ones that lose:
  MAE tables per checkpoint × market × league for all three arms, with n
  (number of projections) per cell; pooled numbers; reconstruction-failure
  count; sample sizes (games, teams, players).

### What this test does NOT do

- **No edge vs any betting line.** There are no odds snapshots and no
  PropLine key. This is a projection-accuracy test only. A pass here means
  the pace input improves projections — it does NOT mean the picks would
  beat a line, and must not be presented that way.

## Deliverables

- This spec (`pace-props-test-spec-2026-11-08.md`)
- Code changes in `engine.py`, `live_feed.py`, `runner.py`, `bovada.py`
  (TEAM_MARKETS only), plus new tests
- `pace_props_test_results.json` — full MAE tables + sample counts +
  pass/fail verdict
- Final report: pace formula and team-prop formula as built, MAE tables per
  checkpoint/market/league for all three projections, whether the pass bar
  was met, plain verdict.
