# Marv Predict Max: multi-sport Telegram bot

Every morning the bot projects the day's games in **NFL, college football, NBA, WNBA, NHL, MLB and
soccer** (EPL, La Liga, Serie A, Bundesliga, Ligue 1, MLS, Champions League). It simulates each game
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
