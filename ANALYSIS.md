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

## Stats-expert backtests (every offense & defense stat, random forest, consensus)

NFL, NBA, WNBA and MLB now run on full box scores: every offensive, defensive and special-teams
column the free data has (131 NFL team stats incl. EPA and passer rating; NBA/WNBA box scores plus
eFG%, TOV%, FT rate, rebound % and offensive/defensive rating per 100 possessions; MLB batting,
pitching and fielding lines plus starting-pitcher form and park factors). Three experts (a
ridge "stat formula", a random forest and opponent-adjusted power ratings) each project the
score; the consensus feeds the Monte Carlo.

Everything is walk-forward (features use only earlier games; models are refit monthly on the
past). Thresholds were picked on the early seasons and then measured on later **test** seasons
the tuning never saw:

| Sport (test seasons) | Every game: winner | Moneyline floor | Test picks | **Test accuracy** | Test ROI | Over/under (test) |
|---|---|---|---|---|---|---|
| NBA (2022-26) | 65.4% (favorite 67.8%) | 86% | 126 | **90.5%** | -1.2% | 52.8% at 17% edge (ROI +0.9%) |
| NFL (2022-25) | 65.8% (favorite 67.6%) | 78% | 30 | **90.0%** | +2.9% | ~49-51%, no edge |
| WNBA (2022-25) | 66.0% | 85% | 26 | **~83-88%** | n/a (no odds data) | no totals data |
| MLB (2023-25) | 56.4% | 70% | 43 | **76.7%** | n/a (no odds data) | no totals data |
| College (top 30) | needs a CFBD key: run `python -m marv stats-backtest --sport cfb --seasons 2016-2025` on the VM | | | | | |

What this means:

* **90% moneyline accuracy is achievable in the NBA and NFL**, and held up on unseen seasons.
  It comes from backing heavy favorites only when all three experts agree and the model is very
  confident: about 25 NBA and 6 NFL picks a season. These are typically -400 to -1000 favorites,
  so the 90% hit rate is roughly break-even after the vig; it is not a money machine.
* **Over/under can't be pushed to 90%.** NFL totals sit at a coin flip at every edge; NBA totals
  show a small edge (54% on tuning seasons, 52.8% on test). Totals are only played for the NBA.
* **MLB tops out around 75%** even on its most confident picks. Baseball is the least
  predictable major sport (even betting favorites win only ~58-60%). Claims of 90%+ MLB
  moneyline or totals accuracy, like the 2025 postseason figures in the PDFs, are not
  achievable without the backtest seeing the results.
* The stat formula (every stat weighted) was the single best expert in every sport; the random
  forest alone was the weakest, but adding it to the consensus helped calibration slightly.

## What was built

One multi-sport engine (`marv/`) covering NFL, college football, NBA, WNBA, NHL, MLB and soccer, with
sport-specific simulators, the shared veto stack, Telegram cards, a graded pick ledger and a daily
systemd timer for a Google Compute Engine VM. See README.md.
