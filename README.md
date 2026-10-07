# Marv College Football Predict Max bot

Simulates the week's marquee college football games, runs every edge through a collegiate veto layer
and sends the qualifying plays to Telegram. It runs on a schedule on a Google Compute Engine VM.

See [ANALYSIS.md](ANALYSIS.md) for the review of the Marv Predict Max PDFs (including why the claimed
90-100% backtests can't be trusted).

## How it works

| PDF component | Implementation |
|---|---|
| Collegiate simulation | `ratings.py`: opponent-adjusted offense/defense ratings fit on completed games (shrunk toward average early in the season; FCS opponents pooled). `simulate.py`: 20,000 Monte Carlo games per matchup, scored drive by drive (TD / FG / nothing). |
| Explosive scoring runs | Per-game gamma "form" multiplier for each team |
| Garbage-time dampener | Team up 21+ entering Q4 scores at 55%; trailing team at 110% (prevent defense) |
| Collegiate pace factors | Plays per game vs. FBS average; changes drive count and expected points |
| Veto logic | `veto.py`: needs a 4% edge over the -110 break-even, 8% when spread ≥ 21, 10% when ≥ 28. Also passes on **trap lines** (moved ≥ 2.5 pts against our side since open), **volatile mismatches** (model > 17 pts from market), and teams with < 2 games. Moneylines need 5% no-vig edge and odds within -400/+400. |
| Top 30 marquee games | Strongest 30 FBS-vs-FBS games with posted lines (`MAX_GAMES`) |

Data comes from the [CollegeFootballData.com API](https://collegefootballdata.com) (free key), using its
consensus line where available.

> The PDF says "Poisson". Pure Poisson touchdown counts produce margins with a ~20-pt standard deviation,
> versus ~16 in real CFB games, which inflates every edge. The drive-based version is the calibrated
> equivalent.

## Setup

### 1. Get your keys
1. **CFBD API key**: free at <https://collegefootballdata.com/key>.
2. **Telegram bot**: in Telegram, message **@BotFather** → `/newbot` → copy the token.
3. Send your new bot any message (e.g. "hi") so it can find your chat.

### 2. Install on your Google VM
SSH into the VM (Compute Engine → VM instances → **SSH**), then:

```bash
curl -fsSL https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/install.sh | sudo bash
sudo nano /opt/marv-cfb-bot/.env        # paste CFBD_API_KEY and TELEGRAM_BOT_TOKEN
cd /opt/marv-cfb-bot
sudo -u marvbot .venv/bin/python -m cfb_bot get-chat-id    # prints your chat id
sudo nano .env                            # paste TELEGRAM_CHAT_ID
sudo -u marvbot .venv/bin/python -m cfb_bot test-telegram  # you should get a message
```

Any small Debian/Ubuntu VM works (an `e2-micro` is enough). It only needs outbound internet access.

### 3. Schedule
The installer enables a systemd timer that runs **Tue 10:00, Fri 10:00 and Sat 09:00 US Eastern**.
Failed runs also send a ⚠️ alert to Telegram.

```bash
systemctl list-timers marv-cfb-bot.timer        # next run time
sudo systemctl start marv-cfb-bot.service        # run now
journalctl -u marv-cfb-bot.service -n 100        # logs
```

To change the schedule, edit `/etc/systemd/system/marv-cfb-bot.timer`, then run
`sudo systemctl daemon-reload`.

## Commands

```bash
python -m cfb_bot run --dry-run                 # print this week's card, don't send it
python -m cfb_bot run --year 2026 --week 7       # specific week
python -m cfb_bot backtest --year 2025           # walk-forward backtest (weeks 4+)
python -m cfb_bot test-telegram
python -m cfb_bot get-chat-id
```

**Run the backtest before trusting any pick.** Each week is predicted using only games played before
it, and results are graded against the actual lines. A long-run win rate of 53-57% against the spread is a genuinely
good model; break-even at -110 is 52.4%.

Tuning knobs (edge thresholds, trap-line size, garbage-time margin and simulation count) are in `.env`; see `.env.example`.

## Development

```bash
pip install -r requirements.txt
python -m unittest discover -s tests
```

*Model output is not a guarantee. Bet responsibly.*
