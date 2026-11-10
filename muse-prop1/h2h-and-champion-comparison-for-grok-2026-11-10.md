# H2H + Champion comparison — full listing for Grok's review

Prepared 2026-11-10. Everything below is from frozen specs written BEFORE
each run. Result files are in this same folder. Log-only backtests; no
bets, no odds, ESPN site API only.

Systems in this listing:
1. The basketball head-to-head (H2H) comparison system + its results.
2. The three-part champion comparison (basketball): champion votes
   standalone, the champion factor with vs without, and the power rank.
3. The college-football Top 12 champion power rank (Nov 8).
4. The college-football H2H — football twin of (1) — design + full
   results of all three arms and its own three-part champion comparison
   (run 2026-11-10, reported to the owner before this push).

---

## 1. The H2H system (basketball)

Spec: `h2h-backtest-spec-2026-11-08.md`. Code: `h2h_backtest.py`.
Results: `h2h_backtest_results.json`.

Design: compare two teams' season averages metric by metric; +1 for each
metric won, 0 on an exact tie; the team with the higher total is the
moneyline pick. The home team gets +1 (home field); on a neutral site no
home point is awarded. Starter injuries are excluded (no reliable
suitable source). Games with a tied total get no pick.

14 metrics: points, rebounds, offensive rebounds, defensive rebounds,
assists, steals, blocks, turnovers (lower wins), fouls (lower wins),
threes made, FG%, 3P%, FT%, assist/turnover ratio.

Pace and opponent points are deliberately NOT metrics: a combined
points+pace index was rejected on 2026-11-08 because it turned a
winning shooter into a loser, and pace is already implied inside
points/rebounds/assists. Averages use only games on strictly earlier
dates (no lookahead).

### Results — college basketball REJECTED, NBA passed

- Arm A — NCAAB 2026-27 (Nov 2-8): 170 games evaluated, 168 picks,
  111 wins, 66.1% vs home-team baseline 79.4%. LOSES.
- Arm B — NCAAB 2026 tournament: 67 games, 66 picks, 44 wins, 66.7%
  vs higher-seed baseline 74.2%. LOSES.
- Arm C — NBA 2026-27 (Oct 20 - Nov 8): 117 picks, 80 wins, 68.4% vs
  home baseline 50.4%. Wins, but a small early-season sample.
- NBA 2025-26 full validation season: 1,212 picks, 748 wins, 61.72%
  vs home baseline 55.39%. The ONLY H2H arm that passed.

## 2. The three-part champion comparison (basketball)

Spec: `h2h-backtest-champion-spec-2026-11-08.md`.
Results: `h2h_backtest_champion_results.json`,
`h2h_backtest_champion_powerrank_results.json`.

Benchmark: for each metric, the mean of the last 3 champions' season
averages (champions identified from ESPN scoreboard data; lookahead
rule: only champions whose seasons finished before the game).

- Part 1 — champion votes as a STANDALONE pick (more champion votes =
  pick, no H2H, no home point): Men A 63.4% (153 picks), Men B 67.7%
  (65), NBA C 57.0% (107) — all below their baselines except the small
  NBA sample. Champion and H2H picks agreed 93% (Men A); in 10
  disagreements H2H won 8, champion won 2.
- Part 2 — champion factor WITH vs WITHOUT (NCAAW arms + NBA 2025-26
  validation): NCAAW A with 67.8% vs without 67.6% (nothing); NCAAW B
  with 71.6% vs without 78.3% (champion HURTS); NBA validation with
  63.58% vs without 61.72% (+1.9 pts). No consistent effect.
- Part 3 — POWER RANK (power score = mean of (team - benchmark) /
  benchmark per metric, flipped for lower-is-better metrics; higher
  score is the pick): pooled 1,807 picks — power rank 64.2% vs binary
  champion vote 62.4% vs baseline 60.9%. Loses to the home baseline in
  EVERY college arm (Men A 64.3% vs 79.4%, Men B 65.7% vs 74.2%,
  NCAAW A 63.7% vs 75.0%, NCAAW B 77.6% vs 86.7%); beats it only in the
  NBA arms (NBA C 66.7% vs 50.4%, NBA validation 63.2% vs 55.4%). One
  real signal: margin quartiles pooled — Q1 (smallest power-score
  gap) 55.2% -> Q4 (largest) 72.4%.

Recorded verdict: the champion factor has NO signal in college.
Power rank beats the binary vote but still loses to picking the home
team in college.

## 3. CFB Top 12 champion power rank

Spec: `top12-cfb-champion-powerrank-spec-2026-11-08.md`.
Results: `top12_cfb_champion_powerrank_results.json`.

The Top 12 of the AP Top 25 (Week 11, Nov 8 2026) ranked by the same
power-score formula, 8 football metrics (points, total yards, rushing
yards, passing yards, turnovers lower-better, takeaways, third-down %,
sacks). Benchmark = mean of the last 3 CFP champions: Michigan 2023,
Ohio State 2024, Indiana 2025. Caveat recorded in the spec: no
points-allowed / yards-allowed — the ESPN core API returns 0.0 for
those fields — so this is an offensive + ball-security rank, not a
complete team rating.

Ranking: 1 Miami +0.0793 (6/8 votes), 2 Wake Forest +0.0351,
3 Georgia +0.0306, 4 Notre Dame +0.0064, 5 Indiana -0.0630,
6 Tennessee -0.0935, 7 Utah -0.1356, 8 Texas Tech -0.1444,
9 UCLA -0.1532, 10 Alabama -0.1830, 11 BYU -0.1831, 12 Texas -0.2573.
Only 4 of 12 above the champion standard.

## 4. College football H2H (new, 2026-11-10)

Spec: `h2h-backtest-cfb-spec-2026-11-10.md`. Code:
`h2h_backtest_cfb.py` (single-window runner used for the short test)
and `h2h_backtest_cfb_full.py` (full arms + champion comparison).
Results: `h2h_backtest_cfb_armA_results.json`, `_armB_`, `_armC_`,
`h2h_backtest_cfb_champion_results.json`, per-game CSVs,
`h2h_backtest_cfb_short_results.json` (the Nov 7-8 pipeline test).

Design: the football twin of section 1 — identical vote / home / pick
rules, identical no-lookahead accumulation, identical arm structure and
three-part champion comparison. 14 metrics, all verified available
per-game from the ESPN summary boxscore: points, total yards, passing
yards, rushing yards, first downs, third-down efficiency, yards per
pass attempt, yards per rush attempt, turnovers (lower), takeaways,
points allowed (lower), yards allowed (lower), penalty yards (lower),
time of possession. Excluded, with reasons in the spec: sacks (not in
the per-game data), fourth-down efficiency (too noisy), completion %
(redundant), rushing attempts (volume). Starter injuries excluded, as
in basketball.

### Results — football H2H beat its baseline in ALL THREE arms

| Arm | Fetched | Evaluated | Skipped | Picks | W-L | H2H | Baseline | Verdict |
|---|---|---|---|---|---|---|---|---|
| A — 2026-27 reg. season | 685 | 538 | 147 | 527 | 336-191 | 63.8% | home 60.2% | PASS (+3.6 pts) |
| B — 2025-26 postseason | 46 | 46 | 0 | 44 | 26-18 | 59.1% | AP-ranked 57.1% | PASS, tiny sample |
| C — 2025-26 validation | 901 | 756 | 145 | 745 | 515-230 | 69.1% | home 59.9% | PASS, clear (+9.2 pts) |

Arm C is the strongest test — a full unseen season, the football
equivalent of the NBA validation arm that was the only basketball arm
to pass — and it passed decisively. College basketball H2H failed this
same test on every angle; football passed it.

Per-metric (H2H): Arm C best = points 67.4%, total yards 64.6%,
points allowed 64.5%; worst = penalty yards 48.3% (below coin-flip),
possession 57.1%, turnovers 57.3%. Arm A best = points 63.3%,
yards/pass 62.8%; worst = penalty yards 45.8%, takeaways 50.5%.
Arm B best = third-down 67.4%; worst = pass yards and rush yards
47.8% each (n=46, noisy).

Arm B honesty: the AP baseline exists only for the 14 games where both
teams were in the final AP Top 25 (Week 16 poll, Dec 7 2025). On that
like-for-like subset H2H went 8-4 (66.7%, 12 picks) vs the baseline's
57.1%, and the power rank went 10-4 (71.4%). On the 32 unranked-bowl
games H2H was 56.2%. Twelve picks is twelve picks — Arm B passes on
paper but proves little by itself.

### Champion comparison (football) — no added signal, one gradient

Benchmark = mean of Michigan 2023 / Ohio State 2024 / Indiana 2025
season averages (12 regular-season games each, accumulated from
per-game summaries; e.g. 39.1 points scored, 10.7 allowed — an elite
bar: only 6.5% of team-games score above it).

- Part 1 (champion votes standalone): Arm A 62.2% (463 picks) vs
  60.2% baseline — beats it. Arm C 67.0% (631 picks) vs 59.9% — beats
  it clearly. Arm B 51.4% (37 picks) — FAILS. Pooled 64.5% (1,131
  picks). It carries signal in the regular-season arms but is strictly
  worse than plain H2H in every arm (63.8 / 59.1 / 69.1). Overlap:
  agrees with H2H 87.6% pooled (corr 0.78); in 138 disagreements H2H
  was right 88 times, champion 50.
- Part 2 (with vs without): adding champion votes to H2H NEVER helps —
  Arm A 63.1% with vs 63.8% without; Arm B 55.6% vs 59.1%; Arm C 68.9%
  vs 69.1%. Self-check: the without-champion recomputation reproduced
  the H2H arm totals exactly (0 mismatches, all arms).
- Part 3 (power rank): Arm A 61.3% (537 picks), Arm B 56.5% (46),
  Arm C 68.9% (755); pooled 65.4% (1,338 picks) — beats the binary
  champion vote pooled (64.5%), roughly ties plain H2H in Arm C,
  trails it in A and B. The one real signal: the margin gradient is
  clean and monotone — pooled win rate by power-margin quartile
  Q1 57.8% -> Q2 59.0% -> Q3 66.8% -> Q4 78.0%; Arm C's Q4 hits 82.7%
  (191 picks).

Verdict: as a pick rule the champion vote beats picking the home team
in the regular-season arms but is dominated by plain H2H everywhere;
as an additive factor it hurts or does nothing in all three arms; the
power rank's only real content is the margin gradient. No case for
adding the champion factor to the H2H system.

### Flag for Grok's judgment (spec-directed, recorded in the results)

The spec assigns all 3 champions to Arm B's benchmark, but Indiana was
not decided champion until the Jan 20, 2026 title game — so December
Arm B games are benchmarked against a not-yet-decided champion. The
spec's parenthetical justifying this has the date wrong; its explicit
instruction (2023+2024+2025 for Arm B) was followed as written. The
flag is also recorded in `h2h_backtest_cfb_champion_results.json`.
