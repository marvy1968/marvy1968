# DEPLOY — muse1/alpaca on the VM

Paper only. Live trading is always blocked in `bot.py` (`--live` exits 2).

## What this is

`muse1/alpaca` — a paper stock bot: day trade + swing trade + scalp, 10
experts, hedge-fund overlay (vol-scaled sizing, portfolio limits, no fixed
1R TP, signal-decay exit). Built 2026-11-05 on Muse's VM, deployed once
there the same day. This push moves it to the VM so it can run there.

## Install

```bash
cp -r muse1-alpaca /opt/marv-bot/muse1-alpaca   # or wherever the VM keeps bots
cd /opt/marv-bot/muse1-alpaca
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Keys — on the VM only, never in git

Create `.env` in the bot folder with the Alpaca PAPER keys (the VM's
existing env file already has them). The `.env` is NOT in this repo and
must never be committed. The bot reads keys from env or `.env` only,
never hard-coded, never printed.

```bash
ALPACA_API_KEY=<paper key>
ALPACA_API_SECRET=<paper secret>
```

Verify before first run:

```bash
venv/bin/python bot.py --dry-run   # exit 0, no orders
venv/bin/python bot.py --live      # must refuse, exit 2
```

## Running it — it is run-once, it needs a schedule

`bot.py --paper` scans once and exits. It does NOT keep scanning. To trade
through the day, run it every 5 minutes inside the windows, e.g. a systemd
timer or cron:

- scalp + day sleeves: 10:00–15:45 ET, flatten 15:45 ET
- swing sleeve: daily bars, once a day is enough (it can carry over)

Example cron (ET, VM local time):

```
*/5 10-15 * * 1-5 cd /opt/marv-bot/muse1-alpaca && venv/bin/python bot.py --paper
```

## HANDOVER — open position (as of 2026-11-05 close)

The Muse VM copy already has ONE open paper position on the shared account:

- **MSFT SHORT x17, entry $537.05, filled avg $537.05**
- Hard stop **$539.83**, virtual trail stop **$534.44** (peak $533.05, +1.44R)
- Unrealized at close: +$44.88 (+0.95R). Equity $51,380.49.
- The 15:45 ET day flatten did NOT happen on 2026-11-05 (bot is run-once),
  so this day trade is carrying overnight. Owner chose "leave it alone".

Whoever runs the bot next owns this position: enforce the trail/stop
(`virtual_exit_check` in `bot.py`) on every check, or close it at the open.
Do NOT ignore it — there is no broker stop order, the stop is virtual.

## DO NOT run both copies on the same paper account

The Muse VM copy and this VM copy must NOT both scan/trade the same Alpaca
paper account. Each copy counts its own max-5-open and max-5-new/day, so
two copies can double-trade and break the limits. Pick one owner for the
account. While the VM copy runs, the Muse VM copy stays stopped.

## Watchlist

Top 3 in every S&P 500 sector by market cap (Nov 4–5, 2026) — 33 stocks
+ SPY (filter symbol). See `config.yaml`. Rankings drift; re-check monthly.

## Rules that are ON by owner choice (not by test)

- SPY filter (`spy_filter.enabled: true`, mode `vs_open`): SPY up = longs
  only, SPY down = shorts only, flat (< 0.15%) = range experts only,
  scalp preferred, swing off. This filter FAILED pre-registered tests on
  other sleeves (Trend PF 0.40 → 0.33, Top-Down 1.51 → 1.46, SPY-vs-open
  halves worse). It is on because the owner asked for it, not because it
  passed a test. Undo: `spy_filter.enabled: false`.
- The bot is UNVALIDATED: no backtest has run on it yet. A pre-registered
  OOS backtest is owed (see the news-dip spec in the goal files), and a
  paper forward test is the real gate — not the backtest.

## Monitoring

The Muse VM ran a 30-min monitor on 2026-11-05 (positions, equity, SPY
latest trade, virtual trail/stop enforcement). That monitor is now
disabled. The VM needs its own equivalent: every 30 min, read positions,
enforce the virtual trail/stop, report SPY price/side, and flatten day/
scalp at 15:45 ET.
