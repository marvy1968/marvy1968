# Marv Predict Max: multi-sport Telegram bot

**Active sports:** NFL · College football (top 30) · EuroLeague · WNBA · NCAA men's basketball ·
NCAA women's basketball. NBA, MLB, NHL and soccer are built but switched off; enable any with
`SPORTS=nfl,cfb,euroleague,wnba,ncaab,ncaaw,nba` in `.env`.

| Sport | Stats source | Live odds |
|---|---|---|
| NFL | nflverse (every team stat, EPA) | nflverse lines, or The Odds API |
| College football (top 30) | CFBD box scores + talent composite (needs `CFBD_API_KEY`) | CFBD lines, or The Odds API |
| EuroLeague | official EuroLeague API (`euroleague-api` package) | **The Odds API only** (`ODDS_API_KEY`) |
| WNBA | ESPN box scores (sportsdataverse) | ESPN, or The Odds API |
| NCAA men's / women's basketball | ESPN box scores for all of Division I (sportsdataverse) | ESPN, or The Odds API |

EuroLeague Women is **not supported yet**: it's run by FIBA, which has no free public data feed,
and The Odds API doesn't list it.

Every morning the bot projects the day's games in each active sport. It simulates each game
thousands of times, prices spreads, totals and moneylines, runs everything through the Max veto stack
and sends the qualifying plays to your Telegram. Every pick is logged and graded, so you build a
real track record.

**Read [ANALYSIS.md](ANALYSIS.md) first.** It explains why the 90-100% win rates in the Marv PDFs aren't
real, and what an honest NFL backtest of this bot showed.

## How each sport is modelled

| Sport | Data (free) | Simulation | Sport-specific pieces |
|---|---|---|---|
| NFL | nflverse games file (scores + lines since 1999) | drive-based TD/FG Monte Carlo | garbage-time dampener, OT |
| College football | CollegeFootballData API (free key) | drive-based, pace-scaled drives | blowout-tier edges (21+/28+ spreads), FCS pooling, top-30 marquee filter |
| NBA / WNBA | ESPN scoreboard | correlated-normal team scores | shared pace component, overtime periods |
| NHL | ESPN scoreboard | bivariate Poisson goals | late empty-net / extra attacker, 3-on-3 OT, shootout (+1 goal settlement), regulation-tie probability |
| MLB | ESPN scoreboard (+ probable pitchers) | inning-by-inning negative binomial | starter (regressed ERA) vs bullpen innings, home skips bottom 9th, extra-inning runner; **vetoes games without confirmed starters** |
| Soccer | ESPN scoreboard | Dixon-Coles scoreline matrix | 1X2 incl. draw, Asian handicaps incl. quarter lines, totals |

### Stats mode (NFL, top-30 college football, NBA, WNBA, MLB)

These sports run on **every offense and defense stat** in their box scores (one stats module
per sport in `marv/stats/`), with three experts — a weighted stat formula, a random forest and
power ratings — whose consensus score goes through the shared Monte Carlo. A play only goes
out when the three experts agree and confidence clears the floor that hit ~90% in backtests:

| Sport | Plays | Moneyline confidence floor | Backtested accuracy (unseen seasons) |
|---|---|---|---|
| NBA | moneyline + totals | 86% | 90.5% ML, 52.8% O/U |
| NFL | moneyline | 78% | 90.0% |
| WNBA | moneyline | 85% | ~83-88% |
| MLB | moneyline | 70% | ~77% (baseball's ceiling) |
| College (top 30) | moneyline | 85% (re-tune on the VM) | run `stats-backtest` |

Other stats-mode vetoes: experts split, fewer than 3 games of stats, unconfirmed MLB starter,
stale stats, and late key injuries.

**Up-to-date data, checked every run:**

| | Stats | Injuries (key players only) |
|---|---|---|
| NFL | nflverse (refreshed every 2 h) | ESPN feed + official NFL report (when it covers this week); starting QB |
| NBA / WNBA | sportsdataverse (2 h) + missing games scraped from ESPN game pages | ESPN feed + official NBA injury-report PDF; top-6 minutes players |
| MLB | MLB Stats API box score of every finished game | ESPN feed + MLB injured-list rosters; top-5 hitters; probable starters |
| College | CFBD (live) | none free; college plays carry no injury check |

* If a team's latest finished game isn't in the stats yet, the game is vetoed ("stats out of date").
* If **every** injury source fails for a sport, all its games are vetoed: no betting blind.
* A **late update** runs at 5:30 PM ET daily (and 11:45 AM ET Sundays) for games starting within
  6 hours, with fresh injuries and stats; its card is labelled "Late update". College ratings
dampen blowout margins beyond 28 points and use the CFBD roster-talent composite.
Set `STATS_MODEL=false` to go back to the ratings-only engine.

```bash
python -m marv stats-backtest --sport nba --seasons 2015-2026          # walk-forward + tuning report
python -m marv stats-backtest --sport cfb --seasons 2016-2025          # needs CFBD_API_KEY
```

Shared engine (`marv/engine.py`) follows the Marv operating sequence:
1. Opponent-adjusted offense/defense ratings with time decay and shrinkage, and a fitted home advantage.
2. Frozen projection and simulation, with no odds involved.
3. **Calibration**: the projection is blended with the market's implied score (`MODEL_WEIGHT`,
   default 0.5) so ordinary model noise isn't flagged as an edge.
4. Odds loaded, vig removed, EV and edge computed for both sides of every market.
5. **Max veto stack**: minimum edge (bigger for blowout spreads), trap-line movement, model/market
   gap, suspicious edge vs the no-vig price, longshot price cap, insufficient history, unconfirmed MLB
   starter, postponed game, simulation instability, and correlated duplicates (spread + ML on the same
   side).
6. Telegram card plus a pick ledger (`state/picks.json`). Graded results show on each card, and a weekly
   record is sent on Mondays.

Odds come from the free sources above. Set `ODDS_API_KEY` to use a consensus across many sportsbooks
from The Odds API instead.

## Setup on your Google VM

1. **Telegram bot**: in Telegram, message **@BotFather** → `/newbot` → copy the token. Then send your
   new bot any message.
2. **College football key** (free): <https://collegefootballdata.com/key>.
3. SSH into the VM (Compute Engine → VM instances → **SSH**), upload `marv-bot.tar.gz` (the ⚙️ menu →
   *Upload file*), then:

```bash
tar -xzf marv-bot.tar.gz && sudo bash marv-bot/deploy/install.sh
cd /opt/marv-bot
sudo nano .env                                           # paste TELEGRAM_BOT_TOKEN and CFBD_API_KEY
sudo -u marvbot .venv/bin/python -m marv get-chat-id     # prints your chat id
sudo nano .env                                           # paste TELEGRAM_CHAT_ID
sudo -u marvbot .venv/bin/python -m marv test-telegram   # you should get a message
sudo -u marvbot .venv/bin/python -m marv run --sport all --force --dry-run   # preview every sport
```

Once the code is on GitHub, the one-line install also works:
`curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/install.sh | sudo bash`

An `e2-small` VM is plenty. It only needs outbound internet access.

### Schedule
A systemd timer runs `marv run --sport all` every day at **10:00 US Eastern**. Each sport sends a card
only when it has games:

| Sport | Runs | Looks ahead |
|---|---|---|
| NBA, WNBA, NHL, MLB, soccer | daily | 26 h |
| NFL | Tue / Thu / Sun | full week / Thursday night / Sun+Mon |
| College football | Tue / Fri / Sat | rest of week / weekend / Saturday |

```bash
python -m marv sports                            # what's enabled and when it runs
systemctl list-timers marv-bot.timer             # next run
sudo systemctl start marv-bot.service            # run now
journalctl -u marv-bot.service -n 200            # logs
```

## Working with an odds-alert bot (e.g. March_edge)

Marv predicts; an odds bot watches prices. They connect through the **Marv bridge**: every Marv
run saves its projections to `state/predictions.json`, and `marv-bridge.service` answers on
`http://127.0.0.1:8787` (local to the VM only).

Before the odds bot sends an alert, it asks Marv for the fair price, pregame or live with the
score and clock:

```bash
python -m marv check --sport nfl --team Lions --market total --side under --line 67.5 --price -110 \
    --home-score 29 --away-score 19 --minutes-left 17.8
# Marv ✅ agrees: fair 69% vs price 52% (edge +17%) · live, projected final total 63.6
curl "http://127.0.0.1:8787/check?sport=nfl&team=Lions&market=ml&side=Lions&price=360&home_score=29&away_score=19&minutes_left=14.9"
```

In the odds bot (Python), block alerts Marv disagrees with and add Marv's line to the rest:

```python
import requests
v = requests.get("http://127.0.0.1:8787/check", params=dict(sport="nfl", team=home, other=away,
                 market="total", side="under", line=67.5, price=-110,
                 home_score=29, away_score=19, minutes_left=17.8), timeout=5).json()
if v["found"] and not v["agrees"]:
    skip_alert()          # Marv's fair chance doesn't beat the price
```

To find the odds bot on the VM and package it (secrets removed) for review:
`sudo bash /opt/marv-bot/deploy/find_odds_bot.sh` then `sudo bash /opt/marv-bot/deploy/find_odds_bot.sh /path/to/bot`.

## Blueprint plugin interface + SQLite audit

`marv/plugins.py` implements the blueprint's `BaseSportPlugin` contract on top of Marv, so a
blueprint-style loop runs on real data:

```python
from marv.plugins import MarvSportPlugin, AuditDatabase
for plugin in [MarvSportPlugin("mlb"), MarvSportPlugin("nba"), MarvSportPlugin("nfl")]:
    for game in plugin.fetch_slate_data("today"):
        projection, confidence = plugin.run_simulation(game)   # projected total, winner confidence
        vetoed = plugin.apply_sport_vetos(game)                  # True unless Marv qualified a play
```

Every Marv run also writes each pick it evaluated, locked or vetoed with reasons, to
`state/marv_bot_audit.db` (table `execution_logs`).

## Commands

```bash
python -m marv run --sport nba --dry-run --force         # preview a sport's card now
python -m marv run --sport all                           # what the timer runs
python -m marv backtest --sport nfl --start 2024-09-01 --end 2026-02-15
python -m marv backtest --sport nba --start 2025-11-01 --end 2026-04-12
python -m marv results --days 30                         # graded record → Telegram
python -m marv test-telegram
python -m marv get-chat-id
```

**Backtest every sport on the VM before trusting it.** The NFL backtest uses real closing lines from
nflverse. ESPN-sourced sports grade against whatever odds ESPN kept for past games. Keep
`PAPER_MODE=true` until the ledger shows a positive record over a few hundred picks.

## Tuning

Everything is in `.env` (see `.env.example`): enabled sports, simulation count, model weight, and
per-sport veto thresholds such as `NBA_SPREAD_EDGE=0.05` or `MLB_MIN_GAMES=15`. Change one thing at a
time and confirm it with a backtest on seasons you didn't tune on.

## Known gaps vs the specs

* No xG, lineup, injury, goalie-confirmation, weather or umpire data: there is no free source for these.
  Ratings are built from scores, so the soccer and hockey specs are only partly covered.
* ESPN's API is public but undocumented. If it changes format, a sport may stop producing cards
  (you'll get a ⚠️ Telegram alert).

## Development

```bash
pip install -r requirements.txt
python -m unittest discover -s tests
```

*Model output is not a guarantee. Bet responsibly.*
