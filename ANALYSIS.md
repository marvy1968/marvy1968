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

## What was built

The CFB bot in `cfb_bot/` implements the CFB Predict Max architecture with a **walk-forward backtest**
(`python -m cfb_bot backtest --year 2025`) that only uses games played before each week. Run it on the VM
to get the model's honest hit rate before betting on it.
