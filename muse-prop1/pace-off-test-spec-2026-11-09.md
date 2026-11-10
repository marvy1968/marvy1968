# Basketball in-game prop pacer — rate-only rework — frozen spec (2026-11-09)

**Status:** FROZEN before any test run on fresh data. User: "Rework the formula
until there is a edge" (2026-11-09). Scope: Muse Prop1, basketball, college
only (see NBA below). No bets, no odds, no keys. Log-only. Local only — do not
push to GitHub.

## Background — what the frozen tests already proved

- Pace as a scoring input is DEAD in both leagues (pace-props tests + decomposition,
  2026-11-08). Pace-only ≈ average everywhere: college player points 3.04 vs 3.00
  avg; NBA player points 3.53 vs 3.53; NBA team points 7.39 vs 7.35.
- The live RATE is the only input with signal, and only in college. Decomposition
  (`pace_vs_avg_test_results.json`, pooled, 510 college games Nov 4–8):
  - Team points: avg 9.28, pace-only 9.30, **rate-only 8.31**, both 8.18.
    Rate-only won 3/3 checkpoints.
  - Player points: avg 3.00, pace-only 3.04, **rate-only 2.85**, both 2.89.
    Rate-only won 3/3 checkpoints.
  - In the NBA (29 games): team points avg 7.35, pace-only 7.39, rate-only 8.37,
    both 8.36; player points avg 3.53, pace-only 3.53, rate-only 3.59, both 3.59.
    Neither input helps; the live rate actively hurts team totals.

## The rework (ONE change)

**Pace off, rate on.** The new formula is exactly the decomposition's rate-only
arm (c), promoted to the formula:

- Teams (`project_team_total`): remaining possessions at SEASON pace
  (`use_live_pace=False` → `blended_pace = season_poss_per_min`); the live
  per-possession rate stays (current / poss_so_far, capped 0.5–1.5× pregame,
  w-blended as before).
- Players (`project_basketball_final`): NO pace-ratio multiplication
  (`use_live_pace=False` → pace ratio 1.0); the live per-minute rate stays
  (capped 0.5–1.5× pregame, w-blended as before). Minutes projection,
  foul factor, and blowout factor are unchanged — they are shared components,
  not part of this rework.
- Everything else frozen: same shrinkage `w = min(0.85, max(0.10,
  elapsed/total_min))`, same caps, same checkpoints (8 / 20 / 30 min), same
  markets (team + player points/rebounds/assists/threes), same priors rule
  (rolling means/SDs from strictly earlier dates only, no lookahead).

Engine change (additive, defaulted, like `use_live_rate`): `TeamPaceInput` and
`BasketballPaceInput` gain `use_live_pace: bool = True`. Default True — every
existing caller (runner, earlier test scripts) is unaffected. Two new tests in
`test_engine.py` cover it. Existing suites must still pass (test_engine 18,
test_muse_prop1 12).

## NBA — PARKED, not reworked

No formula change for the NBA. Both inputs were tested there and neither
helps; the live rate hurts team totals. There is no rework of pace/rate that
creates an NBA edge from these 29 games, and searching for one on the same
data would be overfitting. The NBA arm stays on the shelf until a genuinely
new input (not pace, not the live rate) is proposed as a new frozen test.

## Data — FRESH games only (the verdict data)

- Test window: cached ESPN summaries for college games dated AFTER 2026-11-08
  (the Nov 4–8 window was already used to discover the rate hypothesis).
  Priors come from strictly earlier dates, including the Nov 2–8 games —
  priors are not test data. Exact game counts per league are recorded in the
  results file before any verdict; the window rule itself is frozen here.
- Replay rules, reconstruction validation gate, and exclusions (OT, empty
  plays, reconstruction failures) are exactly as in
  `pace-props-test-spec-2026-11-08.md`. Reuse `pace_props_test.py`'s replay
  functions (imported, not copied — the earlier script is untouched).

## Amendment 2026-11-09 (recorded BEFORE any fetch or run on fresh data)

- Cache inventory 2026-11-09: NO cached college summaries exist after
  2026-11-08. The 2025-26-season files in `h2h_cache` (1,235) are NBA-only;
  `h2h_ncaaw_cache` has no 2025-26 files. The old season therefore cannot
  serve as the fresh window — the fresh data must be fetched live from ESPN.
- Fresh window FIXED: **2026-11-09 to 2026-11-13** (Mon–Fri), NCAAB + NCAAW
  Division I only (scoreboard groups=50), completed games only. Fetched each
  morning after the games complete (`fetch_fresh_summaries.py`, idempotent —
  skips already-cached summaries). Priors come from strictly earlier dates,
  including the Oct 20–Nov 8 cache.
- Verdict test runs the morning of 2026-11-14, after the Nov 13 fetch. The
  pass bar and self-check above are unchanged. No game dated Nov 9–13 is
  examined before the verdict run.

## Self-check (must pass before any verdict)

Run the new rate-only arm on the OLD Nov 4–8 window: it must reproduce the
decomposition's rate-only numbers to 4 decimal places (team points pooled
8.31, player points pooled 2.85), proving the flag implements exactly the
decomposition's (c) arm. If any cell differs, the run is INVALID.

> Amendment 2026-11-10 (does not change the pass bar or the fresh window):
> the cached summaries grew after Nov 8 (527 games in the Nov 4–8 window
> vs 510 when the decomposition ran), so the frozen self-check numbers are
> stale. The self-check now compares against the decomposition re-run on the
> current cache: team points 8.2407, player points 2.8373
> (pace_off_test.py SELFCHECK_EXPECTED). Verified 2026-11-10: the
> decomposition re-run reproduces these exactly, and the decomposition's
> own self-check fails on college but passes on NBA (unchanged data) —
> confirming data drift, not a code change. The flag implements the (c)
> arm exactly.

## Pass bar (pre-registered, fresh window only)

College (NCAAB + NCAAW pooled, as in the college test), team points AND
player points: the rate-only arm beats the average arm (season pace + season
rate, i.e. the (a) arm) at **≥2 of 3 checkpoints AND pooled**. Report all
markets and every checkpoint, including the ones that lose. The (a) arm is
the baseline; the old pace-adjusted arm (d) is reported for reference only
and does not count toward the pass.

## If it fails

The rate hypothesis is dropped for college too. No re-tuning on the same
data. Next candidate single changes, each a NEW frozen test on fresh data:
(1) shrink the live rate harder (cap the w-blend for the rate); (2) rate-only
for teams, pace-naive for players (split by market). "Rework until there is
an edge" means running the pre-registered candidates in sequence — NOT
tweaking the same data until a backtest turns green.

## What this test does NOT do

No edge vs any betting line. No odds snapshots, no PropLine key. Projection
accuracy only. A pass means the rate-only formula predicts finals better than
the season average on fresh games — it does NOT mean picks would beat a line.

## Deliverables

- This spec (`pace-off-test-spec-2026-11-09.md`)
- Additive engine change (`use_live_pace`) + 2 new tests; earlier scripts untouched
- `pace_off_test.py` — replay script (imports replay machinery from
  `pace_props_test.py`)
- `pace_off_test_results.json` — full MAE tables per league × checkpoint ×
  market × arm, sample counts, self-check vs the decomposition, verdict per
  the frozen rule
