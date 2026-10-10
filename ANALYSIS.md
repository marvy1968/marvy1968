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

## The owner's Pro Football Max formula: original vs fixed vs Marv (NFL 2016-2026)

`marv/stats/pfm.py`. Fixes: scoring drives fitted from real games (~6.2, not 2.4); a team's scoring rises
with the opponent's EPA allowed (the original subtracted it); fitted home field (+1.8 pts); ties count
half; point-in-time EPA blended with last season. Parameters fitted only on earlier seasons.

| 2,736 games | Winners | Brier | 70%+ picks | 78%+ picks | Avg projected total (miss) |
|---|---|---|---|---|---|
| Original formula | 45.1% | 0.264 | none | none | 16.8 (28.9 pts) |
| Fixed formula | 63.3% | 0.226 | 77.1% (358) | 83.8% (68) | 45.8 (10.8 pts) |
| Marv | **64.5%** | **0.221** | **78.5% (395)** | **86.7% (83)** | |

Held-out 2022-26: fixed 63.2% vs Marv 65.4%; over/under vs closing totals 48.9% (no edge); the original
veto (injuries, weather, tight spread) lowered 70%+ accuracy (74.7% vs 78.0%). The original's
coin-flip-or-worse result comes from the defense sign. A 50/50 blend with Marv didn't beat Marv alone.

## The owner's head-to-head total engine (pace, points per drive, red zone, Poisson)

On its own Cowboys–Bucs example it projects 78.7 points (line 47.5). Backtested on 2,736 NFL games
2016-2026 with real point-in-time inputs from nflverse play-by-play:

| Model | Avg projected total | Avg miss | O/U vs closing total |
|---|---|---|---|
| Head-to-head engine as written | 66.0 | 24.1 | 49.4% |
| Head-to-head engine fixed | 47.4 | 11.7 | 49.2% |
| Pro Football Max fixed | 45.8 | 10.8 | 48.3% |
| Marv | 45.7 | 10.7 | 49.3% (51.1% when 3+ pts off the line, 570 games) |
| Closing total itself | 45.2 | **10.4** | - |

Actual average 45.6; break-even at -110 is 52.4%. Problems in the original: drives = pace / 11.5
(~5.7 per team; real ~10-11) times a points-per-drive *rate* inflated by +1.8, then counted again as
3.2-point scoring events; subtracting the opponent's points allowed (a bad defense should add); and the
red-zone multiplier double counts what points per drive already includes. No version beats the closing
total: NFL totals stay a no-bet in Marv.

**Normalized version (tuned 2016-21, tested 2022-26).** Every input is relative to the previous season's
league average and combined multiplicatively, `points = league ppd x (offense/league)^a x (opponent
allowed/league)^b x (red zone/league)^c x drives(pace)`, with a, b, c fitted. Fitted a = 0.55 (offense),
b = 0.28 (defense); red zone and pace came out near zero (they add nothing beyond points per drive).
Held-out: average projected total 43.6 (actual 45.0), miss 10.47 points (same as Marv; closing line 10.22),
over/under vs closing totals 49.5% (50.2% when 4+ points off the line). Blending with the closing line
was best at 100% market weight. Tethered to reality, but still no over/under edge.

## Situational spots (prime-time overreaction, rest/travel, key numbers, divisional dogs)

Tested against closing spreads and real spread prices: 2006-15 (discovery) vs 2016-26 (confirmation).

| Angle | 2006-15 | 2016-26 | Verdict |
|---|---|---|---|
| Fade last week's prime-time blowout winner | 44.2% (156) | 46.1% (154) | Backwards: backing them went 54.8% of 310 (p=0.19) |
| Back last week's prime-time blowout loser | 48.8% | 50.4% | No edge |
| Rested team, 4+ day rest edge | 50.5% | 51.6% | No edge |
| Home team vs West Coast visitor, 1:00 PM ET | 52.1% (94) | 44.0% (125) | Flipped; no edge |
| 4+ day rest edge + 2 time zones of travel | 48.3% (87) | 54.3% (94) | Inconsistent |
| Divisional home dog +3.5 or more | 48.6% | 50.5% | No edge |
| Non-division home dog +3.5 or more | 56.8% (229) | 54.3% (258) | 55.4% of 487 (p=0.09), but 46.8% in 2024-26 |
| Dogs at +3 / +7 / +2.5 / +7.5 | 55.9 / 48.9 / 52.1 / 53.8% | 51.9 / 49.0 / 52.1 / 53.8% | No stable key-number edge |

Margins of exactly 3 (14.6%) and 7 (8.9%) are common, as the key-number idea says, but the closing
prices around those numbers already reflect it. With ~15 angles tried, one or two p < 0.10 results are
what luck alone produces. Nothing here qualifies as a bet trigger; the two best (prime-time blowout
winners, non-division home dogs 3.5+) are worth tracking in paper mode only.

## Advanced play-by-play features (EPA split, early-down success, explosives, pressure, EMA)

Built from nflverse play-by-play for every team-game 2009-2026, offense and defense: pass EPA per dropback,
rush EPA per carry, early-down (1st/2nd) success rate, overall success rate, explosive rate (15+ yard
passes, 10+ yard runs), pressure rate (QB hits + sacks per dropback, the closest nflverse has to pressure),
EPA on pressured dropbacks. Recency-weighted with an EMA (half-life tuned on 2012-15: 6 games), only
earlier games, matchup = my offense + their defense. Ridge model refit each season on the 6 before it.

| Held-out 2016-26 (2,736 games) | Winners | Brier | 70%+ picks | 78%+ picks | ATS when 4+ pts off the spread |
|---|---|---|---|---|---|
| Advanced features | 63.9% | 0.2232 | 74.5% (593) | 82.1% (196) | 50.1% (587) |
| Marv | 64.5% | 0.2214 | 78.5% (395) | 86.7% (83) | 54.4% (463, p≈0.19) |
| 50/50 blend | 64.5% | 0.2215 | 75.7% (469) | 83.5% (115) | |
| Closing moneyline (no-vig) | **66.5%** | **0.2108** | 79.0% (1,037) | 82.5% (469) | |

The stable efficiency stats don't beat Marv's full box-score set, and neither beats the market: the
~64% "side ceiling" is what public stats can reach; the closing line (66.5%) also prices injuries,
weather and news. Not adopted.

## Weighting each head-to-head stat

Instead of 1 point per stat won, a ridge regression learns each stat's weight from the 6 seasons before
each test season ("learned weight per stat"), or also uses how big each edge is ("weights x size of edge").

| Held-out | Equal weights | Learned weights | Weights x size of edge | Marv |
|---|---|---|---|---|
| NFL 2016-26 (2,641) winners | 63.7% | 63.9% | **64.6%** | 64.7% |
| NFL 80%+ picks | 83.2% (101) | 91.7% (24) | 91.8% (49) | 91.5% (47) |
| College FB top 30, 2018-26 (1,432) winners | 76.5% | 76.9% | **77.7%** | 77.7% |
| College FB 80%+ picks | 88.4% (596) | 87.2% (587) | 88.9% (522) | **92.2% (448)** |
| ATS when 55%+ sure (NFL / CFB) | 50.6% / 47.2% | 51.5% / 48.4% | 50.6% / 49.2% | |

Weighting by the size of each edge adds about 1 point of winner accuracy over the 1-point-per-stat tally
and brings the head-to-head method level with Marv, not past it (Marv's confident college picks stay
better). In college football the heaviest weights went to points per drive, red-zone TD rate and
defensive rush success / yards per play allowed; turnovers were weighted near zero (too noisy).
Against the spread, no version has an edge. `marv/stats/h2h.py` now supports `mode="weighted"` and
`mode="magnitude"`.

## Recalculating the head-to-head data points (weights, top stats, opponent strength, last-3 blends)

After learning each stat's weight, the head-to-head points were recalculated several ways and re-run
through the walk-forward backtest (each season learned from the 6 before it, same games):

| Held-out | Winners NFL / CFB | 80%+ picks NFL / CFB | O/U every game NFL / CFB |
|---|---|---|---|
| Equal (1 point per stat) | 63.7% / 76.5% | 83.2% (101) / 88.4% (596) | |
| Weights x size of edge (current) | **64.6%** / 77.7% | **91.8% (49)** / 88.9% (522) | 49.9% / 50.4% |
| Top 8 weighted stats only, refit | 64.1% / 77.8% | 82.3% (113) / 89.4% (530) | |
| Every stat adjusted for the opponents' strength | 63.3% / 77.4% | 84.6% (65) / 89.3% (561) | 50.9% / 52.1% |
| 75% season + 25% last 3 | 64.0% / 77.8% | 88.7% (53) / 89.2% (536) | 49.9% / 49.9% |
| 75/25, opponent-adjusted | 63.5% / 77.1% | 89.2% (65) / 90.2% (572) | 50.9% / 50.6% |
| Last 3 only, opponent-adjusted | 60.9% / 73.5% | 83.3% (18) / 88.7% (467) | 50.5% / 50.6% |
| Marv | 64.7% / 77.7% | 91.5% (47) / **92.2% (448)** | |

* Winner differences are within the error bars (about ±1.8 NFL, ±2.2 college), except that last-3-only
  is clearly worse and opponent adjustment costs NFL 1.25 points (95% range -2.4 to -0.3).
* Opponent adjustment helps college calibration a little (Brier 0.163 -> 0.161, 80%+ picks up to 90%),
  since college schedules are uneven; in the NFL it adds noise.
* Pruning to the biggest weights doesn't help: ridge already shrinks the weak stats. NFL's top weights
  were turnover and kicking stats that change from season to season, a sign the NFL tally is mostly noise.
* Totals stay at 50-52% in every version, below the 52.4% needed at -110; projected totals miss by more
  than the closing line (NFL 10.8 vs 10.5, college 12.8-13.4 vs 12.5). Against the spread: 49-52%.

Verdict: keep weights x size of edge with the learned season + last-3 columns; no version passes Marv.
`h2h.walk_forward(..., adjust=1, blend=0.25)` and `h2h.breakdown()` (each stat's weighted points for a
game) are available for paper tracking.

## Recency weighting: last 25% of the season, exponential decay

Gemini suggested weighting the last 25% of the season (or a compressed rolling window) into the Monte
Carlo, especially for totals. Tested on the same held-out games, magnitude weights, raw and
opponent-adjusted (scratch script; "last 25%" = the most recent quarter of the games a team has played
this season, at least 2):

| Held-out | Winners NFL / CFB | 80%+ picks NFL / CFB | Spread 55%+ NFL / CFB | O/U NFL / CFB |
|---|---|---|---|---|
| Season + last 3 (current) | **64.6%** / 77.7% | 91.8% (49) / 88.9% (522) | 50.6% / 49.2% | 49.9% / 50.4% |
| Season + last 25% | 64.2% / 77.8% | 87.2% (47) / 88.9% (515) | 51.1% / 50.2% | 49.6% / 51.0% |
| 75% season + 25% last quarter | 63.8% / 77.2% | 90.7% (43) / 88.4% (510) | 50.3% / 48.7% | 49.7% / 50.2% |
| Exponential, half-life 3 games | 61.7% / 74.7% | 86.4% (22) / 88.7% (433) | 49.4% / 47.2% | 49.3% / 50.9% |
| Season + last 25%, opponent-adjusted | 63.3% / 77.7% | 84.1% (63) / **90.9% (540)** | 50.3% / 51.2% | 49.8% / 50.7% |
| 75/25 last quarter, opponent-adjusted | 63.7% / 77.7% | 89.1% (64) / 90.5% (535) | 50.6% / 50.2% | 50.7% / 50.8% |
| Exponential, opponent-adjusted | 61.7% / 75.5% | 91.7% (36) / 90.0% (478) | 49.6% / 47.8% | 49.9% / 50.4% |

No recency version beats the current one on winners, and none gets the spread or totals to 52.4%.
Heavy recency (exponential decay) is clearly worse: a few games are too small a sample. Projected
totals miss by more than the closing line in every version. Not adopted for live O/U; the one mild
positive (college 80%+ picks at ~91% with opponent adjustment) is still below Marv's 92.2%.

## Team over/under trends and the trend Monte Carlo vs closing totals

Does a team's recent over/under record, or the head-to-head Monte Carlo total built from recent-form
stats, predict the next game's result against the closing total? NFL 2010-26 (4,382 games with a
closing total), college FB 2017-26 (6,727 FBS games); Monte Carlo totals are walk-forward predictions.

| Rule (win rate vs the closing total, -110) | College FB | NFL |
|---|---|---|
| Both teams over in 2+ of their last 3 -> bet OVER | 45.1% (994) | 49.7% (849) |
| Same games -> bet UNDER (fade the trend) | **54.9% (994, p=0.002)** | 50.3% |
| Same, games with a top-30 team | **56.6% (325)** | |
| Both teams' last 3 beat the total by 14+ combined -> UNDER | 54.6% (683) | 51.2% (422) |
| Both teams' season over rate 60%+ -> OVER | 42.7% (314) | 45.8% (297) |
| Monte Carlo total (season + last 3), 55%+ sure | 51.5% (3,369) | 48.8% (1,450) |
| Monte Carlo total (last 3, opponent-adjusted), 55%+ sure | 50.4% (3,780) | 50.4% (1,663) |
| Monte Carlo agrees with the team trend | 44.7% (1,039) | 49.5% (610) |
| Monte Carlo disagrees with the trend -> follow Monte Carlo | 52.6% (821) | 52.3% (501) |

* Following a team's over/under trend loses. In college football the opposite works: the more overs
  both teams had in their last 3 combined, the more often the next game went under (0/6 overs: 40%
  under, 3/6: 51%, 5/6: 57%, 6/6: 63%). The closing total seems to over-adjust to recent high-scoring
  games. The fade beat 52.4% in 8 of 9 full seasons (2024 was 48.5%), and same-week random games went
  under only 50.9% (p=0.008).
* The NFL shows no such pattern; its totals are sharper.
* The trend Monte Carlo has no totals edge by itself, and when it agrees with a team's streak it loses,
  because it is chasing the same recent scoring the line already priced.
* Caveat: ~24 rules were tested on two sports, so the best one is partly selected by luck; prices are
  assumed -110 (college juice varies). Paper-track "fade both-teams-over-trending college totals"
  before staking anything.

## Continuation or reversal: market over-adjustment, scoring luck and pace

Gemini's idea: an over/under trend continues when it's backed by volume and the market under-reacted,
and reverses when the market moved the total more than the underlying efficiency justifies. Tested per
game, pre-game only: market move = this closing total minus the average total of each team's earlier
games (last season worth 4 games + this season's games before the last 3); yardage move = the change in
the teams' game yardage over the last 3 x league points per yard (0.069 college, 0.063 NFL);
over-adjustment = market move minus yardage move. Scoring luck = points beyond what the yardage usually
produces. College 4,024 games, NFL 3,767 (needs 3+ earlier games that season).

| Rule (win rate vs the closing total) | College FB | NFL |
|---|---|---|
| Over trend (both teams 2+ overs in last 3) -> UNDER | 54.4% (924) | 51.2% (916) |
| Over trend + market over-adjusted (2+ pts beyond yardage) -> UNDER | **56.5% (310)** | 54.3% (234; 50.0% early, 58.6% late) |
| Over trend + market matched -> OVER (continuation) | 46.6% (614) | 49.9% (682) |
| Over trend from scoring luck -> UNDER | 55.0% (462) | 53.5% (458) |
| Over trend from pace (plays up 5+) -> OVER | 42.6% (216) | 42.4% (132) |
| Under trend (both 0-1 overs in last 3) -> OVER | 54.3% (1,120) | 48.8% (1,018) |
| Under trend + market matched -> UNDER (continuation) | 45.3% (691) | 50.5% (792) |
| Any game: total moved 5+ pts more than yardage justifies -> UNDER | 52.2% (737) | **55.8% (400; 55.4% / 56.1% by half)** |

* "Continuation" never paid: in college football both over and under trends reverse whether or not the
  market over-adjusted. Over-adjustment and scoring luck only make the reversal stronger.
* Pace doesn't make a trend sticky either: over trends driven by more plays went over only 42-43% next.
* NFL: the trend itself has no edge, but a total that rose 5+ points more than recent yardage justifies
  went under 55.8% (p=0.024), steady in both halves of 2009-26.
* About 30 rules were tested here; the leads worth paper-tracking are college "fade both trends" and
  NFL "total inflated 5+ beyond yardage -> UNDER". Neither is proven.

## Line movement, Bovado vs sharp books, and closing-line value (college football)

Data: opening and closing lines from ~25 books for every FBS game (sportsdataverse cfbfastR-data,
`marv/data/cfb_lines.py`); opening lines for 8,971 games with results 2014-25 (none in 2020), Pinnacle /
BetCRIS closing numbers 2014-19, Bovado lines and prices throughout. Ticket and money percentages
(betting splits) have no free history anywhere, so public-vs-sharp splits can't be backtested; line
movement is the measurable trace of that money.

| Test | Win rate (n) | Notes |
|---|---|---|
| Follow a 2+ pt spread move, bet at the close | 48.7% (3,136) | fading: 51.3% |
| Favorite's number shrank 1.5+ ("money on the dog") -> dog at the close | 49.6% (2,038) | reverse-line-move proxy: no edge |
| Total fell 1.5+ -> UNDER at the close | 52.0% (2,811) | 53.7% to 2019, 50.1% since 2021 |
| Bet the open on the side the spread later moved toward | 55.8% (5,639) | only if you can predict the move |
| Bet the open on the side the total later moved toward | 58.3% (4,917) | same |
| Spread: Bovado 0.5+ pts better than Pinnacle/BetCRIS -> bet it at Bovado | 53.2% (2,429) | ROI +0.6% after Bovado's real juice |
| Total: Bovado 0.5+ pts off Pinnacle/BetCRIS -> better side at Bovado | **54.4% (2,340)** | **ROI +4.6%** (2014-19) |
| Same spread side graded at the sharp number (control) | 48.7% (842) | the edge is the number difference |
| Spread moved toward the head-to-head model's side (model 3+ off the open) | 56.0% (3,161) | "closing-line value" |
| ...but the model's side against the spread at the open / close | 50.2% / 49.4% | the CLV doesn't turn into wins |
| Over trend (both teams 2+ overs in last 3) -> UNDER | 53.5% (1,829) | ROI +2.1% |
| Over trend + total rose 1.5+ from the open -> UNDER | **56.7% (298)** | ROI +8.3%; 58.6% to 2019, 54.3% since |
| Under trend -> OVER | 51.2% (2,050) | weaker with consensus closes than in the earlier test |

* Moves are fully priced by the close: following or fading them at the closing number does nothing.
  The value of a move is only available before it happens (55.8% ATS at the open).
* The model's picks show closing-line value (lines move toward it 56% of the time) yet don't win at the
  open. The markets move toward public stats that the model shares; the moves that matter (injuries,
  news) aren't in its inputs. CLV alone can overstate a stats model.
* The one usable consensus signal is line shopping against the sharp books: when Bovado's total is half a
  point or more off Pinnacle/BetCRIS, the better side at Bovado went 54.4% (+4.6% ROI). Spreads were
  break-even after Bovado's juice. Live Pinnacle prices are in The Odds API's "eu" region.
* The over-trend fade gets stronger when the total has also been bid up during the week (56.7%), which
  fits the market over-adjusting to recent shootouts. ~40 rules tested in this section: paper-track.
* NFL: no free opening-line history; on the VM, The Odds API historical endpoint can supply open and
  close (credits).

## Backup-QB flag (NFL)

Every NFL game where either team's latest starting QB has fewer than 3 starts (this and last season, any
team) or its starting QB is ruled out is now skipped for every bet. Backtest 2016-26 (nflverse starters):

| | Games | Winners | 78%+ picks | Marv 4+ pts off the spread, ATS |
|---|---|---|---|---|
| Normal starters | 2,308 | 64.2% | 87.1% (62) | 54.3% (302) |
| Backup QB in the game | 507 | 65.9% | 85.7% (21) | 54.4% (169) |

Marv disagrees with the spread more in backup-QB games (3.4 vs 2.2 points on average), but its results in
those games were no worse, so the flag isn't a proven improvement: it's a safety rule against betting
into injury news the stats lag behind (e.g. Tampa Bay's Jalon Daniels, Oct 8 2026: market Dallas -8.5
to -10, Marv Dallas by 3).

## NFL simulator calibration (key numbers and spread of outcomes)

Every 2016-26 game simulated at its closing spread and total, compared with real finishes:

| | Old sim | Calibrated sim | Real games |
|---|---|---|---|
| Games decided by exactly 3 | 8.0% | ~13-14% | 14.7% |
| Decided by exactly 7 | 6.6% | ~8.5% | 8.5% |
| Spread of totals around the closing line (SD) | 14.2 | 13.3 | 13.2 |
| Spread of margins around the closing spread (SD) | 13.8 | 12.7 | 12.7 |

Changes (`marv/sims/football.py`, NFL only): real extra-point outcomes (7/6/8), a last-possession phase
that plays the score (field goal when tied or down 1-3, touchdown when down 4-8), NFL overtime (57% end
by 3, 6% tie), less game-to-game "form" swing (shape 30 -> 120), and a calibrated transfer of near-miss
finishes onto 3 and 7.

Backtest with Marv's own projections (2,815 games, 2016-26):

| | Old | Calibrated |
|---|---|---|
| Moneyline Brier | 0.2221 | 0.2219 |
| Push probability on spreads of 3 (real 10.2%) | 4.5% | 7.1% |
| Push probability on spreads of 7 (real 6.4%) | 3.8% | 4.8% |
| Against the spread, log loss (coin flip = 0.693) | 0.7048 | 0.7086 |
| Over/under, log loss | 0.7033 | 0.7044 |
| ATS when 55%+ sure | 51.2% (1,437) | 51.8% (1,628) |

The simulator now reproduces real NFL scores and prices pushes far better, which matters for spreads on
3 and 7, live prices and quarter bets. It does not make Marv's spread or total picks better: with
either simulator they are worse than a coin flip in log loss, because Marv's projections disagree with
the closing line mostly where the line is right. NFL spreads and totals stay off as picks.

## NFL player props vs real sportsbook lines (2025)

`props-backtest --seasons 2025 --real --max-credits 6000`: real Bovado/DraftKings/FanDuel lines one hour
before kickoff, 4,128 priced props (credit cap reached partway through the season).

| Market | Edge 0%+ | Edge 5%+ | Edge 10%+ |
|---|---|---|---|
| All markets | 49.1% of 3,040, ROI -5.5% | 48.7% of 1,640, ROI -5.1% | 48.8% of 857, ROI -4.6% |
| Passing yards | 51.7% (201) | 46.3% (95) | 48.9% (47) |
| Receiving yards | 50.3% (1,132) | 50.3% (581) | 51.9% (289), ROI -0.9% |
| Receptions | 46.2% (1,074) | 45.5% (525) | 41.5% (234), ROI -11.7% |
| Rushing yards | 51.2% (633) | 50.8% (439) | 51.6% (287), ROI -2.5% |

Always betting the under went 52.5%, better than the model. The 55-63% earlier came from lines built from
averages; real books set props near the median and price in usage and matchups. Bigger model edges did
not win more often. No prop edge: prop cards are off by default (PROPS=on to restore them).

## Market-anchored confidence and where the pick engine can (and can't) improve

Walk-forward, expanding window, held-out seasons (NFL 2018-25 n=2,219; college top-30 2018-25 n=2,336;
logistic fits on earlier seasons only). Script: scratchpad `stack.py`, `stack2.py`, `stack3.py`.

| Question | Result |
|---|---|
| Does the model add information beyond the closing spread? (stacked logistic) | **No.** NFL model coefficient 0.00-0.01 vs market 0.14; CFB 0.00 vs 0.12. Brier: market 0.2108, stack 0.2109, model alone 0.2204 |
| Does it add to the total? (actual total − line regressed on model total − line) | **No.** NFL weight −0.20, CFB −0.02; top-20% edge picks hit 50.2% / 50.4%. NBA lines in the file are unreliable so NBA was not judged |
| Moneyline accuracy at equal volume (NFL, top 400 by confidence) | model alone 76.5% (ROI −6.6%), market 84.3% (−0.4%), 20% model + 80% market 84.8% (+0.2%) |
| Same, ≥85% confidence | NFL 88.6-90.5%, CFB 93.4% (900 games) vs 88.6% / 91.5% for the model alone |

What changed: stats-mode moneyline confidence is now `0.2 × model + 0.8 × no-vig market`
(`StatsRules.ml_model_weight`, NFL and college football; floors 0.85). Accuracy at a given volume goes up because the
engine stops treating model noise as confidence on games the market already prices at 70-90%.

What did not and cannot change: this is not new skill. It's the market's information, so ROI stays
break-even (−2% to +0.2%), and over/unders have no edge from the model (50%). Gains that can still be tested:
college basketball and WNBA (need the VM's line history to run the same stack), injury/lineup news the
market hasn't priced, and the paper-tracked trend tags (college O/U fades, Bovado-vs-Pinnacle totals).

## Injury-trend adjustment backtest (NFL 2018-2025, 2,761 games)

Point-in-time test of `marv/regime.py` (new starting QB or WR1 out: scale the team's projected points by its
scoring in the new situation, shrunk n/(n+2), capped 25%). Only games before each game were used; the
affected games were 464 (765 QB-change and 322 WR1-out team-games).

| | Base projection | With trend adjustment | Market |
|---|---|---|---|
| Total MAE, all games | 10.69 | 10.77 | 10.44 |
| Total MAE, affected games | 10.80 | **11.27** | 10.50 |
| Margin MAE, affected games | 9.87 | **10.36** | 9.43 |
| Over/under direction, affected | 48.8% | 48.8% | |
| Following the adjustment vs the line (moved 2+ pts, n=294) | 50.7% | | |

Result: the adjustment makes projections worse and has no betting value, so it is now shown as an info note and
**not applied** unless `REGIME_ADJUST=on`. The backup-QB veto (fewer than 3 starts) is unchanged.

## Power grades (0-100) and matchup gaps: college football FBS vs FBS, 2018-2025 (4,507 games)

Method: every team-game box stat (EPA, success, explosiveness, stuff, sacks, turnovers, points per drive, red zone and more; offense = own
stats, defense = what opponents did against the team) is averaged to date with a prior-season carryover, z-scored per season,
and a ridge model trained on earlier seasons only turns them into an offense power and a defense power. Teams are graded 0-100 by
percentile within the season. Not included: the depth x blue-chip multiplier (needs CFBD /talent, not available here; to do on the VM).

| Higher-grade team, gap in grade points | Wins outright | Covers | Market favorite wins (same games) |
|---|---|---|---|
| 0-5 | 53.4% | 50.9% | 62.3% |
| 10-15 | 67.3% | 54.6% | 66.3% |
| 20-30 | 70.6% | 48.7% | 73.2% |
| 40-60 | 81.8% | 49.0% | 82.9% |
| 60-100 | 89.2% | 48.7% | 89.5% |

* The grade gap predicts winners cleanly, but the market favorite wins as often or more (73.0% vs 69.9% overall). Covers sit at about 50%
  in every bucket, including 90+ vs 70-80 (n=179: wins 72.1% vs the market favorite's 77.7%, covers 48.9%). No moneyline or spread edge.
* **One lead: totals.** Define a scoring grade = average of both offense grades and the inverse of both defense grades. Games at 59 or
  higher (top 20%) went **Under 56.5% in training (2018-21, n=398), 55.7% on the held-out 2022-25 (n=436), 55.9% in 2024-25 (n=222)**
  against a 52.4% break-even at -110 (about +6% ROI). By season the top-quintile Over rate was 46.6, 47.3, 46.4, 36.7, 44.5, 45.8, 51.3, 35.7%.
* Monte Carlo: shuffling the Over/Under labels 3,000 times, a quintile this lopsided shows up by chance p = 0.0013 (5 quintiles tested).
  Replacing the grade with random numbers and repeating the discover-on-train, test-on-holdout pipeline 1,000 times, the real holdout
  (55.7%) beat 98.9% of the random runs (random mean 49.4%, sd 2.5%).
* Caveats: the holdout 95% interval (about 51-60%) still touches break-even; it is the same family as the existing OVER-FADE tags (the
  market overprices scoring), so it is probably the same edge, not an additional one; closing totals only, no Bovada prices.
  Treat as a paper-tracked lean until 100+ graded live bets stay above 52.4%.

### Top-10 games per week by combined grade (CFB FBS vs FBS)

Rule fixed before testing on earlier years: each Monday-Sunday week, the 10 games with the highest combined grade (higher + lower team's grade).

| Sample | Games | Higher-grade team wins | Market favorite wins | Covers | Under |
|---|---|---|---|---|---|
| Newest 105 (Nov 2025-Oct 2026) | 105 | 62.9% | 68.6% | 49.5% | 60.0% |
| All 2025-26 weeks | 175 | 65.7% | 70.9% | 54.4% | 56.6% |
| **2018-2024 (95 weeks)** | 851 | 67.8% | 73.0% | 51.0% | **52.5%** |
| 2018-2024 scoring grade 59+ | 140 | 66.4% | 72.9% | 47.1% | 56.0% (n=134, overlaps the 2018-21 discovery years) |
| 2018-2024 all other games | 3,010 | 70.4% | 73.1% | 49.6% | 49.8% |

The 2025-26 "top matchups go Under about 60%" did not replicate: on 2018-2024 it is 52.5% (+0.2% ROI at -110), above only 87% of
random 10-game weekly sets. By season the Under rate was 50.9, 58.8, 53.5, 55.9, 45.5, 50.0, 52.6%. The grade-gap and top-10
moneyline/spread results stay at or below the market favorite and 50% covers. The scoring-grade Under lead (56% on the 2018-2024
overlap, 55.7% on the true 2022-25 holdout) is the only survivor and remains a paper-tracked lean.
