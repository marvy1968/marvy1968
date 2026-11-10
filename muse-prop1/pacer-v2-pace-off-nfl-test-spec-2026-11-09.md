# Pacer v2 pace-off fix, NFL arm — frozen test spec

Date: 2026-11-09. Author: Muse. Status: FROZEN before any test run on the pacer's graded rows.
Code: pacer_v2 in marvy1968/marvy1968, branch `cursor/pacer-v2-e0ae` (PR #4, merged).
Change: the `--no-pace` flag already implemented for the NBA arm (see
`pacer-v2-pace-off-test-spec-2026-11-08.md`). No production code change was
needed for NFL — the flag is sport-agnostic and covers football rows; what is
new here is the NFL proof (unit test `test_pace_off_covers_nfl_rows`,
`verify_nfl_pace_off.py`) and this frozen spec.

## Background

- Live paper record (Kalshi, `nfl_prop_live`, from the 2026-11-08 paper report,
  arithmetic verified): 56–52, +$1,025, +19%, 108 bets — the most credible lane
  in the whole Kalshi/Polymarket book. Unlike the NBA arm (106–137, −$1.79,
  breakeven — "fix or cut"), the NFL arm is already winning. The pace-off
  variant is a candidate improvement to a working lane, not a rescue: if it
  fails, it is dropped and the live lane is left exactly as it is.
- Frozen pace tests (2026-11-08, `~/workspace/muse-prop1/`): pace as a scoring
  input is dead for player props in both leagues. College player points:
  pace-only 3.04 vs season-average 3.00 (worse). NBA player points: pace-only
  3.53 vs 3.53 (identical). The live rate is the only input with signal, and
  only in college. There is no league-specific reason to expect NFL pace to
  behave differently — but the NFL arm is the one lane where a wrong change
  costs real (paper) profit, so it gets its own frozen test, not a ride on the
  NBA result.

## How the NFL arm works, and why the existing flag covers it

- Pacer v2 scores NFL props through the same `project()` path as NBA props.
  `sport_of()` returns `"football"` when the row stat is in `py/ry/recy/rec/td`
  (parse.py `FOOTBALL_STATS`); the clock is 60 minutes / 15-minute quarters,
  the foul factor is ignored for football, and blowout thresholds are
  football-specific (|margin| ≥ 28 → 0.35, ≥ 17 → 0.50). None of that touches
  pace.
- Pace enters only through `production_shift` in `adjust.py`:
  `context = minute_ratio * def_mult * pace_mult`, shift = delta / stat scale
  (football scales: py 45.0, ry 18.0, recy 16.0, rec 1.5, td 0.45). `pace_mult`
  comes from `context_multipliers` → `_pace_mult`: the game override, else the
  mean of the two teams' pace factors from team_stats.json, else 1.0.
- The `--no-pace` flag (`pace_enabled=False`, threaded through `decide` /
  `fit_model` / `recalibrate` on `score` / `report` / `backtest` / `recalib`)
  forces `pace_mult = 1.0` in `project()` with no sport check, and records the
  gap `pace_disabled`. Verified on a synthetic NFL row (stat py, game pace
  1.25): pace on → pace_mult 1.25, p_pacer 0.875315; pace off → pace_mult 1.0,
  p_pacer 0.800000, exactly the no-team-stats baseline. Pace-off is NOT a
  no-op for NFL: pace moves NFL probabilities whenever team-stats pace is
  present (here +0.075). Whether it moves in live production depends on the
  VM's team_stats.json actually carrying NFL pace entries — see Data, below.

## Single change (Test 1)

**Pace off.** Force `pace_mult = 1.0` in the production shift. Nothing else changes:
same v1 probability, same minutes/blowout/foul/b2b factors, same defense multiplier,
same market blend weight, same isotonic map, same 0.15 edge gate, same overs band,
same timing filter. Implemented as the existing `--no-pace` flag.

## Data (BLOCKER — not in hand)

The pacer's own graded rows: `/opt/marv-feed/reports/pacer_v2/graded_nfl.jsonl`
on the VM (path corrected 2026-11-10 per Grok — was
`/opt/marv-bot/state/pacer/graded.jsonl`, which does not exist).
Not in the repo, not accessible to Muse. No synthetic rows and no Prop1 replay data
may stand in for the verdict — Prop1's replay has no market prices, no
`p_model_raw`, and no graded outcomes per prop line, so it cannot drive the pacer
backtest. The test runs only when the graded file (plus the VM's `team_stats.json`,
so the pace factor is actually present in the control arm) is obtained.

Two honesty notes about this file:
- The graded file may mix sports. Run the NFL test on the NFL subset only:
  extract rows whose stat is a football stat (`py/ry/recy/rec/td`) into
  `nfl_graded.jsonl` first; the split and the fit are within that subset.
- Whether the live `nfl_prop_live` lane is even scored by pacer v2 is NOT
  verifiable from this VM — the lane name carries no "pacer", and its engine
  and config live on the VM. The frozen test does not depend on that: it runs
  the tool's own `backtest` on the graded NFL rows and compares v2 pace-on vs
  v2 pace-off vs v1 on the same rows. If the graded file contains no NFL rows,
  or the live lane turns out to run a different engine, this test cannot
  verdict the live lane — say so and stop, do not substitute other data.

## Split and procedure

Use the tool's own `backtest` on `nfl_graded.jsonl`: chronological 60/40
game-disjoint split, fit (blend weight, overs band, isotonic) on train only,
score test only. Run it twice on the same file, same split, same team-stats:
- Control: `backtest` (pace on, current behaviour).
- Variant: `backtest --no-pace`.

Validity check (not a pass-bar item): count control-arm test rows with
`pace_mult != 1.0`. If fewer than 10 test rows carried a non-1.0 pace, the
comparison is vacuous — pace was never actually on in the control arm — and
the verdict is INCONCLUSIVE, not a pass or a fail. Fix the team-stats file and
re-run once; do not call it a win for either side.

## Pass bar (pre-registered, test half only)

All of these on the TEST half (NFL rows only):
1. v2-no-pace ROI > 0 AND ROI with 2c slippage > 0.
2. v2-no-pace test n ≥ 30.
3. v2-no-pace test ROI beats v2-with-pace test ROI.
4. v2-no-pace test ROI beats v1 test ROI.
Train numbers are reported for both arms but do not count toward the pass.

## If it fails

The variant is dropped. No re-tuning on the same graded data. The live NFL lane
keeps running exactly as it is — a failed test changes nothing live. Next
candidate single changes, each a NEW frozen test on the same procedure, in this
order:
- Test 2: unders only (drop the over side; the overs band is the most-fitted part).
- Test 3: edge gate 0.15 → 0.20.
- Test 4: drop the isotonic map (identity only).
Stop at the first pass, or when the candidates are exhausted. "Test until positive"
by re-tuning the same data is overfitting and is NOT done — a positive manufactured
that way would not hold live.

## Scope

Log-only. No orders, no stakes, no deploy. Muse cannot touch the live pacer or the
VM; a pass means a PR for Claude to deploy, and the live paper record is what
confirms it afterward. A backtest pass is a hypothesis, not a deployment.
