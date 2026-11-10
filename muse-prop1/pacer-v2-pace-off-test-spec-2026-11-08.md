# Pacer v2 pace-off fix — frozen test spec

Date: 2026-11-08. Author: Muse. Status: FROZEN before any test run on the pacer's graded rows.
Code: pacer_v2 in marvy1968/marvy1968, branch `cursor/pacer-v2-e0ae` (PR #4, merged).
Change branch (prepared, untested on real data): `muse/pacer-pace-off` — see the `--no-pace` flag.

## Background

- Live paper record (Kalshi, `nba_prop_live_pacer`, from the 2026-11-08 paper report):
  106–137, −$1.79, 243 bets — breakeven. The most action in the Kalshi book, zero return.
- Frozen pace tests (2026-11-08, `~/workspace/muse-prop1/`):
  pace as a scoring input is dead for player props in both leagues.
  College player points: pace-only 3.04 vs season-average 3.00 (worse).
  NBA player points: pace-only 3.53 vs 3.53 (identical). The live rate is the only
  input with signal, and only in college (team points rate-only 8.31 vs avg 9.28);
  in the NBA the live rate actively hurts team totals (8.37 vs 7.35).
- Pacer v2 shifts P(over) by the team pace multiplier (`pace_mult` from the
  team-stats file, via `production_shift` in `adjust.py`): delta = rate × remaining
  × (minute_ratio × def × pace − 1), shift = delta / stat scale, applied in logit
  space. For player props that shift is noise by the frozen evidence above.

## Single change (Test 1)

**Pace off.** Force `pace_mult = 1.0` in the production shift. Nothing else changes:
same v1 probability, same minutes/blowout/foul/b2b factors, same defense multiplier,
same market blend weight, same isotonic map, same 0.15 edge gate, same overs band,
same timing filter. Implemented as a `--no-pace` flag on `score` / `report` /
`backtest` / `recalib`, threaded through `decide` / `fit_model` / `recalibrate`
as `pace_enabled=False`. The decision records the gap `pace_disabled`.

## Data (BLOCKER — not in hand)

The pacer's own graded rows: `/opt/marv-feed/reports/pacer_v2/graded.jsonl`
on the VM (directory corrected 2026-11-10 per Grok — was
`/opt/marv-bot/state/pacer/graded.jsonl`, which does not exist; the NFL
graded file in that directory is `graded_nfl.jsonl`, confirm the NBA
filename on the VM before running).
Not in the repo, not accessible to Muse. No synthetic rows and no Prop1 replay data
may stand in for the verdict — Prop1's NBA replay has no market prices, no
`p_model_raw`, and no graded outcomes per prop line, so it cannot drive the pacer
backtest. The test runs only when the graded file (plus the VM's `team_stats.json`,
so the pace factor is actually present in the control arm) is obtained.

## Split and procedure

Use the tool's own `backtest`: chronological 60/40 game-disjoint split, fit
(blend weight, overs band, isotonic) on train only, score test only. Run it twice
on the same graded file, same split, same team-stats:
- Control: `backtest` (pace on, current behaviour).
- Variant: `backtest --no-pace`.

## Pass bar (pre-registered, test half only)

All of these on the TEST half:
1. v2-no-pace ROI > 0 AND ROI with 2c slippage > 0.
2. v2-no-pace test n ≥ 30.
3. v2-no-pace test ROI beats v2-with-pace test ROI.
4. v2-no-pace test ROI beats v1 test ROI.
Train numbers are reported for both arms but do not count toward the pass.

## If it fails

The variant is dropped. No re-tuning on the same graded data. Next candidate
single changes, each a NEW frozen test on the same procedure, in this order:
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
