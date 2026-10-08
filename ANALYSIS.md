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
| NCAA men's basketball (2023-25, D1 vs D1) | 71.4% | 79% | 4,260 | **90.3%** | n/a here (VM pulls lines) | needs historical lines (VM) |
| NCAA women's basketball (2023-25, D1 vs D1) | 76.2% | 76% | 7,076 | **90.8%** | n/a here (VM pulls lines) | needs historical lines (VM) |
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
* **College basketball** has such large talent gaps that 90% moneylines are common (about 1,400 men's
  and 2,400 women's picks a season), all on strong favorites. Strength-of-schedule adjusted stats made
  the stat formula clearly better than power ratings alone (men's 73.8% vs 71.0% winners; women's 76.8%
  vs 73.2%). Profit after the vig still has to be checked with historical lines on the VM.
* The stat formula (every stat weighted) was the single best expert in every sport; the random
  forest alone was the weakest, but adding it to the consensus helped calibration slightly.

## What was built

One multi-sport engine (`marv/`) covering NFL, college football, NBA, WNBA, NHL, MLB and soccer, with
sport-specific simulators, the shared veto stack, Telegram cards, a graded pick ledger and a daily
systemd timer for a Google Compute Engine VM. See README.md.

## WNBA "Hybrid H2H Matrix & Trend Catcher" report (2026 playoffs) vs Marv

The report replaces model side-picks with a winner-take-all tally across every offense, defense,
shooting and ball-security category, adjusted by a 3-game "trend catcher" (turnover spikes and
rebounding drops versus the season average). It claims ~78% ML/ATS and ~65% O/U, but it gives no
sample, weights or seasons. Its "backtest" is three 2026 first-round series described after the fact.

Walk-forward test on every WNBA playoff game 2012-2026 (271 games). All features use only games
played before tip-off, and the trend weight was fit on 2012-19 only:

| Playoff winner accuracy | Home team | **Marv (current)** | H2H tally | H2H + trend (report) |
|---|---|---|---|---|
| 2012-2026 (271) | 62.0% | **65.3%** | 61.3% | 61.6% |
| 2020-2026 held out (133) | 63.9% | **68.4%** | 60.9% | 60.9% |
| 2025 (24) | 62.5% | **58.3%** | 37.5% | 37.5% |
| 2026 so far (12) | 83.3% | **75.0%** | 50.0% | 41.7% |

* The category tally does worse than simply picking the home team. Counting categories throws away
  how big each edge is and counts correlated stats (FG%, eFG%, points, offensive rating) several times.
* The trend catcher gave no measurable lift. Larger trend weights made it worse (55-59%), and adding
  it to Marv's margin chose a weight of zero. Three-game swings in WNBA box scores are mostly noise.
* Totals: the possession-loop total (13.6 MAE) was no better than Marv's (13.3 MAE). 50.6% of playoff
  games finished under Marv's number, so there's no O/U edge in either direction (historical lines
  needed for a true O/U win rate; run on the VM). The report's 195.5-197.5 Liberty-Dream total is far
  outside the WNBA range: both models projected ~175, and the game finished 174.
* Marv's 85% WNBA moneyline floor produced zero playoff picks (playoff games are close). Its 70%+
  playoff picks went 85.1% (40/47), the closest honest analogue to the report's claim.

Verdict: keep Marv's engine. Don't adopt the tally or trend modifier.

## Max Pick head-to-head method (stat-by-stat tally + last-3 trend + Monte Carlo)

`python -m marv h2h-backtest --sport nfl|cfb|... --seasons 2016-2025`. Every offense stat and every
stat allowed on defense is compared team vs team: a point to the better team (e.g. yards per play allowed
4.4 vs 5.0). Which way is "better" is learned from earlier seasons. The same tally is repeated on each
team's last 3 games, last season's averages carry in for early weeks, and the tally becomes a margin
that the shared Monte Carlo turns into win, cover and over/under probabilities. College football now
gets its stats free from play-by-play (yards per play, EPA, success rate, explosiveness, third downs,
red zone, field position, turnovers...), so it no longer needs a CFBD key.

Walk-forward, same games, Marv vs the head-to-head method (held-out seasons):

| | Marv ML (all / 80%+ picks) | H2H ML (all / 80%+ picks) | Spread best | O/U best |
|---|---|---|---|---|
| NFL 2022-25 | 66.2% / 100% (15) | 63.7% / 75.8% (33) | ~53-55%, noisy | ~50% |
| College FB top 30, 2021-25 | 75.6% / 90.2% (285) | 73.2% / 84.9% (351) | ~49-52% | ~50-51% |
| WNBA 2022-26 | 68.0% / 85.2% (108) | 66.5% / 85.2% (142) | no lines | no lines |
| NBA 2022-26 | 65.4% / 84.2% (431) | 64.0% / 83.2% (321) | n/a | ~50% |

The tally is close to Marv but slightly worse everywhere: counting categories throws away how big each
edge is. Neither method beats closing totals.

**2026, last 3 finished weeks** (rules fixed on 2017-2025 first, nothing re-tuned on these games):

| | Marv ML 70%+ | H2H ML 70%+ | Marv strict rule | ML every game | O/U |
|---|---|---|---|---|---|
| NFL weeks 2-4 (48 games) | 87.5% (7/8) | 81.8% (9/11) | 1/1 | 58.3% | 39.6% |
| College FB top 30, weeks 4-6 (64 games) | 91.9% (34/37) | 89.5% (34/38) | 93.3% (14/15) | 81.2% | 51.6% |

The college football picks hit the 90% target on unseen games. NFL is close on very few picks. Tuning
until these specific games reach 90% would only fit the past and wouldn't carry forward.

## NFL player props

`marv/props/`: passing, rushing and receiving yards plus receptions. Projection = player form (weighted
recent, season, last 3), usage (attempts, carries, targets, target and air-yards share), the opponent's
yards allowed to that position vs the league, and the implied team total from the spread and total.
A gradient-boosted model per market, then a Monte Carlo drawn from the model's own past misses gives
P(over). Picks need edge >= 5% over the price's implied probability (probabilities pulled 30% toward
50% until the real-line backtest calibrates them).

Walk-forward 2022-2026 (free data, no real lines available here):

| Market | Avg miss: model / season avg / recent form | vs an average-based line, edge >= 10% | always-under on that line |
|---|---|---|---|
| Passing yards | 65.0 / 65.8 / 66.0 | 63.0% (1,293) | 50.8% |
| Rushing yards | 27.4 / 28.0 / 27.2 | 60.8% (1,709) | 58.1% |
| Receiving yards | 25.7 / 27.4 / 26.3 | 65.0% (3,121) | 58.7% |
| Receptions | 1.8 / 1.9 / 1.8 | 65.3% (3,244) | 57.6% |

The model clearly beats the average-based approach of props_analyzer.py (which went 47-51%), but these
lines are naive: averages sit above typical outputs, so "always under" already wins ~58% outside passing
yards. Sportsbook lines are set near the median and are much sharper, so expect far lower real win rates.
The real test is `props-backtest --real` on the VM (Odds API historical props from 2023).

## NCAA men's basketball player props

`marv/props/ncaab.py`: points, rebounds, assists and made threes for Division I players (20+ minutes).
Projection = player form (weighted recent, season, last 3), minutes, shot volume and starter rate, the
opponent's output allowed to that position group vs the league, both teams' pace and scoring, home/away.
Same gradient-boosted model and Monte Carlo of past misses as the NFL props.

Walk-forward 2022-2026 (285,559 player-games per market; no real lines available here):

| Market | Avg miss: model / season avg / recent form | vs average-based line, edge >= 10% | always-under | props_analyzer rule |
|---|---|---|---|---|
| Points | 4.63 / 4.73 / 4.72 | 62.6% | 52.3% | 50.2% |
| Rebounds | 1.90 / 1.95 / 1.93 | 63.6% | 53.6% | 52.8% |
| Assists | 1.21 / 1.24 / 1.23 | 65.2% | 54.8% | 55.4% |
| Made threes | 0.85 / 0.87 / 0.86 | 72.7% | 62.6% | 62.2% |

2025-26 season alone, points at a 10% edge: 62.8%. Points is the most believable signal (only ~52% from
leaning under). Real sportsbook lines are sharper than these average-based lines; the real test is
`props-backtest --sport ncaab --real` on the VM, limited to games involving a top-50 team (a full D1
season of props would cost ~200k credits).

## Quarter-by-quarter in-game model (walk-forward, held-out seasons)

At the end of each quarter (half in NCAA men's), the model projects the final margin and total from the
pregame line, the score, and the game's own stats so far. It's a ridge correction on top of the original
score-and-pace method; a model trained from scratch on game stats (boosted trees) did worse than the
original method and was dropped.

| Sport (games per checkpoint) | Checkpoint | Winner: stats model / original | Brier: stats / original | Final-total miss (pts): stats / original |
|---|---|---|---|---|
| NFL 2020-26 (1,752) | Q1 / Half / Q3 | 69.3 / 69.2 · 77.1 / 77.3 · 82.2 / 82.0% | 0.196 / 0.196 · 0.156 / 0.157 · 0.126 / 0.126 | 9.6 / 10.5 · 7.8 / 7.9 · 6.2 / 6.4 |
| College FB 2020-26 (3,774) | Q1 / Half / Q3 | 77.3 / 77.4 · 81.5 / 81.4 · 86.3 / 86.4% | 0.153 / 0.153 · 0.127 / 0.126 · 0.096 / 0.097 | 11.1 / 12.0 · 8.8 / 9.0 · 6.9 / 6.9 |
| WNBA 2021-26 (1,626) | Q1 / Half / Q3 | 71.2 / 70.0 · 76.6 / 77.3 · 82.8 / 82.5% | 0.187 / 0.192 · 0.153 / 0.154 · 0.117 / 0.118 | 11.9 / 12.3 · 9.9 / 10.0 · 7.3 / 7.3 |
| NCAA men's 2022-26 (29,944) | Half | **80.3 / 78.8%** | **0.135 / 0.142** | **9.4 / 10.3** |
| NCAA women's 2022-26 (27,795) | Q1 / Half / Q3 | 78.8 / 77.9 · 82.8 / 82.5 · 87.6 / 87.3% | 0.143 / 0.149 · 0.117 / 0.120 · 0.086 / 0.087 | 11.2 / 11.6 · 9.0 / 9.5 · 6.7 / 6.8 |

* **College basketball is where game stats help**: shooting, rebounding, turnovers and pace at halftime
  add 1.5 points of winner accuracy and nearly a point off the total projection on ~30,000 games.
* **Football gains come from recalibration, not stats**: a score-only version of the same correction
  does as well, so yards per play and turnovers add nothing beyond the score.
* The over/under rates in the backtest output are against the *pregame* total, which is easy once the
  score is known; real live totals move with the score, and no historical live lines were available
  to test against.

## Stress tests (all findings)

**Player props, against a sportsbook-style line.** The "book" here is a competent baseline projection
(player form and usage, regressed) hung at its median, which is how books set lines. As a control, a
model trained on shuffled outcomes scores about 50% against this line. Held-out seasons, edge >= 5%:

| Sport | Market | Marv win rate (bets) | 95% interval | Every season |
|---|---|---|---|---|
| NFL 2024-26 | pass yds / rush yds / rec yds / receptions | 59.2% / 57.1% / 57.1% / 59.5% | 55-62% | 55-62% |
| College FB 2023-26 | pass / rush / rec / receptions | 57.9% / 57.2% / 55.1% / 57.3% | 53-60% | 53-59% |
| WNBA 2023-26 | points / rebounds / assists | 58.8% / 58.8% / 59.1% | 57-60% | 57-61% |
| NCAA women's 2024-26 | points / rebounds / assists | 58.8% / 59.0% / 62.8% (70k-108k bets) | 58-63% | flat |
| NCAA men's 2024-26 | points / rebounds / assists | 58.0% / 59.4% / 62.2% (68k-116k bets) | 58-62% | flat |

* **90% is never reached on a real-looking line.** The best validation slices topped out at 70-85% on a
  few hundred bets and gave back several points on held-out seasons.
* **Made threes looked like 97-98%. That's an artifact, not an edge**: they are unders at 0.5 for players
  who rarely shoot threes. A book won't hang that line at -110. Live props now cap probabilities at
  15-85%.
* **What the numbers mean.** Marv's edge over a *simple* book is real and stable. Bovado prices with
  more information (injuries, minutes news, sharp action), so the real win rate will be lower. Only
  `props-backtest --real` on the VM measures that. Even 54-55% at -110 would be profitable.

**Game moneyline picks (the ~90% findings).** Bootstrap intervals, season-by-season results and
threshold sensitivity hold up for college football (91.7%, 95% interval 88-96%) and NCAA men's (90.2%,
89-91%). NFL (28 picks) and WNBA (61) are too small to be sure: lowering the NFL cutoff from 78% to 74%
drops it to 82%. The market's own biggest favorites, taken in the same numbers, did as well or better
(college FB 94.5%, NFL 100%). These picks are heavy favorites the market already prices in.

**Recommendation engine on real closing prices.** No game market (NFL moneyline/spread/total, college
football spread/total, NBA moneyline/spread/total) produced a positive, statistically significant
held-out ROI. NBA totals came closest (+9.4% on 157 bets, p = 0.11). So the board shows game markets as
leans at most and hides the ones that lost money.

**Bugs the stress tests caught and fixed:** NBA spreads before 2022-23 were unsigned in the source file
(about a third of games had the favorite backwards), and average-based "lines" were beatable even by a
model that knew nothing, which inflated earlier props results.

## Elimination filters, injuries/roster, weather and home advantage

"Eliminate a pick if the favorite doesn't meet expectations" (the Gemini idea) and every other filter was
chosen on early seasons and measured on later ones the filters never saw.

**NFL** (picks chosen on 2016-21, tested on 2022-26; ROI at real closing moneylines):

| Rule | Held-out accuracy | Picks/season | ROI |
|---|---|---|---|
| Marv 70%+ | 75.4% | 35 | -8.4% |
| + drop if 1.5+ full-time starters out, wind 15+ mph, or market under 70% | **78.4%** | 23 | -7.0% |
| Marv agrees with the market and market 85%+ | **93.4%** | 15 | +3.5% (avg price -950) |
| best rules that hit 90% on 2016-21 | 85-87% | 6-8 | -3% to -4% |

* Starters out (snap-weighted, from injury reports) and the QB being out are real effects: those
  favorites won 67-72% instead of ~81%. Adding them as model features didn't help (65.1% -> 64.5% overall);
  as *eliminations* they add about 3 points.
* Favorites that "missed expectations" over their last 3 games (Gemini's rule) won 89% in validation, about
  the same as everyone else, so that filter removes good picks; it's not used for the NFL.
* Weather (wind, temperature, roof), rest and home-field advantage were already model inputs.
* The only way to 90%+ is agreeing with a heavy market favorite, priced around -950: break-even.

**NCAA men's basketball** (chosen on 2021-23, tested on 2024-26): favorites missing 15%+ of their regular
minutes (players who sat out the last game) won 77.9% vs 83.3%. An 80% floor with the experts agreeing held
**90.8% on 3,907 held-out picks (~1,300 a season)**; the roster filter is applied live as well.

**WNBA**: ~13 picks a season, too few to tune; dropping favorites in a slump went 89.6% vs 86.5% held-out
(48 picks), not enough to adopt.

Live: `marv/stats/registry.py` now applies the NFL eliminations (market < 70%, 1.5+ starters out, wind
15+) and the NCAA men's 80% floor with the missing-minutes veto (`marv/data/roster.py`).
