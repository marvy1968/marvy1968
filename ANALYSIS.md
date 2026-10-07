# Analysis of the Marv Predict Max documents

Five PDFs were reviewed: College Football Predict Max, the Predict Bot System Blueprint (CFB metric
ledger), Soccer Predict Max (Expanded), Hockey Predict Max and Pro Baseball Predict Max.

## The headline problem: the backtest numbers are not achievable

| Document | Claimed result | Reality check |
|---|---|---|
| CFB Predict Max | 100% spread, 100% O/U on 27/24 plays (3 weeks) | Sharp, profitable bettors hit ~54-58% ATS long-term. Books shade lines to ~50/50. |
| CFB "old linear" baseline | 86.7% ATS, 80% O/U on 30 games | Also far above any known public or professional model. |
| Baseball Predict Max | 97-100% moneyline, 96-100% O/U, 188 plays | MLB favorites rarely exceed 70% true win probability; a 98% ML hit rate is not possible without information leakage. |

A 100% (or 98%) hit rate on a few dozen bets is the signature of **look-ahead leakage or in-sample fitting**,
not of a real edge. Common causes:

1. Ratings built from season-long stats that include the games being "predicted".
2. Thresholds or vetoes chosen after seeing which games won (the veto "passes" the losers).
3. Grading against closing lines while the model saw post-game or closing information.
4. Tiny samples: 24-30 plays over 3 weeks can't distinguish 60% skill from luck, let alone prove 100%.

The Soccer and Hockey specs get this right ("Never force a 90% hit rate; report the genuine out-of-sample
result", strict point-in-time data, CLV/Brier/calibration tracking). **That standard should apply to all
of the Marv models**, including the CFB and baseball ones. Realistic targets: 53-57% against the spread or
totals, positive closing-line value (CLV) and calibrated probabilities. Anything above about 60% sustained
should be treated as a bug to hunt down.

## Document-by-document

**CFB Predict Max**: a sound core idea (Monte Carlo instead of linear point estimates, explicit
garbage-time and pace handling, and a veto layer that passes 20-30% of the slate). It is under-specified:
no data sources, rating method, simulation parameters, edge thresholds or definition of a "trap line".
The bot in this repo fills those gaps with concrete, testable choices (see README).

**System Blueprint (38-metric ledger)**: counting "categories won" throws away magnitude (winning rush
defense by 0.1 yd counts the same as by 80 yds), double-counts correlated stats (PPG, total offense and
PPA all measure the same thing) and the Top-30 opponent filter leaves 2-4 games per team, which is too few. It's
better used as a feature list feeding opponent-adjusted ratings than as a scoring system. Note that standard
CFB home-field advantage has fallen to ~2-2.5 points, not 3.

**Soccer Predict Max (Expanded)**: the most rigorous spec, with a frozen architecture, Dixon-Coles
scoreline matrix, markets derived from one distribution and walk-forward validation. The blocker is data: xG,
PPDA, lineups and keeper PSxG need a paid feed (e.g. Opta/Stats Perform, Sportmonks, API-Football) or
scraping sites whose terms forbid it.

**Hockey Predict Max**: also rigorous. The goalie-confirmation gate, empty-net modelling and separate
regulation and OT outcomes are the right priorities. The free NHL API (`api-web.nhle.com`) covers schedules,
scores, standings and probable goalies. Odds would come from The Odds API.

**Baseball Predict Max**: the plate-appearance Monte Carlo plus a 60-65% veto rate is a reasonable design,
but the reported 97-100% results need to be discarded and re-tested walk-forward. MLB Stats API (free) and
Statcast/pybaseball cover the data; odds from The Odds API.

## Honest NFL backtest of this bot

nflverse has every NFL game since 1999 with closing lines, so the bot was tested walk-forward: each
week was predicted using only earlier games and graded against the real closing line.

| Seasons | Spread (after veto) | Totals (after veto) | Moneyline (after veto) | Brier: model vs market |
|---|---|---|---|---|
| 2018-2023 (used for tuning) | 128-130, -2.9% ROI | 145-173, -10.6% ROI | 19-34, -19.4% ROI | — |
| 2024-2025 (held out) | 41-37, +1.3% ROI | 47-42, +1.5% ROI | 10-12, +3.0% ROI | 0.2205 vs **0.2059** |

What this means:

* **The market beats a scores-only model.** Closing-line win probabilities are more accurate than the
  model's (lower Brier). Across 2018-2023, weighting the model more never improved on the market alone.
* **The veto stack works as a filter**, not as an edge generator. It passed 85-89% of the slate and cut
  losses versus betting every model edge (spread ROI -4.1% → -2.9%; moneyline bets cut from 1,657 to 53),
  but six seasons of results don't show a profitable edge.
* The small 2024-2025 profit is within noise for ~80 bets per market.
* Longshot moneylines were the biggest leak (the favorite-longshot bias), so NFL underdogs are capped
  at +150 and other sports at +250.

That's why the bot ships in **paper-trading mode**: it alerts and grades every pick, and a real edge
has to show up in its own ledger (ideally including beating the closing line) before money goes on it.
Edges more realistically come from information the market hasn't priced yet: early lines before they
move, confirmed lineups, goalies and pitchers, injuries and weather. The specs list these, but they need
paid data feeds.

The other sports couldn't be backtested from the build environment (ESPN wasn't reachable there).
Run `python -m marv backtest` for each sport on the VM.

## What was built

One multi-sport engine (`marv/`) covering NFL, college football, NBA, WNBA, NHL, MLB and soccer, with
sport-specific simulators, the shared veto stack, Telegram cards, a graded pick ledger and a daily
systemd timer for a Google Compute Engine VM. See README.md.
