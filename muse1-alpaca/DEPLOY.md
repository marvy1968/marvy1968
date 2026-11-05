# DEPLOY — muse1/alpaca on the VM

Paper only. Live trading is always blocked in `bot.py` (`--live` exits 2).

## What this is

`muse1/alpaca` — a paper stock bot: day trade + swing trade + scalp, 10
experts, hedge-fund overlay (vol-scaled sizing, portfolio limits, no fixed
1R TP, signal-decay exit). Built 2026-11-05 on Muse's VM, deployed once
there the same day. This push moves it to the VM so it can run there.

**Version 0.2.0 (2026-11-05) — Claude fix list applied:** real bracket
exits (entry + stop + target in one order), `state.json` + `bot.lock`,
fail-closed broker reads, `feed=iex` + completed bars only, market
calendar, one expert log-only (`raschke_soup`), flatten 15:50 ET.
See README.md "v0.2.0 — what changed" for the full list.

## Install

```bash
cp -r muse1-alpaca /opt/marv-bot/muse1-alpaca   # or wherever the VM keeps bots
cd /opt/marv-bot/muse1-alpaca
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Keys — on the VM only, never in git

**The bot MUST use its own Alpaca paper account, separate from Brain.**
Create a NEW Alpaca paper account for muse1/alpaca (Muse cannot create
it — only the user/Claude can), then create `.env` in the bot folder
with THAT account's paper keys. Do NOT put Brain's paper keys in this
bot's `.env`: the two bots share nothing, and running muse1 on Brain's
account is not supported (it would double-count positions, trades, and
the daily loss cap against Brain's book). The `.env` is NOT in this repo
and must never be committed. The bot reads keys from env or `.env` only,
never hard-coded, never printed.

```bash
ALPACA_API_KEY=<muse1-only paper key>
ALPACA_API_SECRET=<muse1-only paper secret>
```

Verify before first run:

```bash
venv/bin/python bot.py --dry-run   # exit 0, real Alpaca bars, no orders
venv/bin/python bot.py --live      # must refuse, exit 2
venv/bin/python bot.py             # no flag: does nothing, exit 0
python3 test_fixes.py              # 37 unit checks, no keys, no network
```

## Running it — it is run-once, it needs a schedule

`bot.py --paper` scans once and exits. It does NOT keep scanning. To trade
through the day, run it every 5 minutes inside the windows, e.g. a systemd
timer or cron:

- scalp + day sleeves: 10:00–15:45 ET, flatten 15:50 ET
- swing sleeve: daily bars, once a day is enough (it can carry over)

The bot takes a lock file (`bot.lock`) so overlapping runs cannot
double-trade; a second run while one holds the lock exits 0 without
trading. State (`state.json`, local only, never in git) is created on
the first `--paper` run — it starts empty on the VM. **Before the
first VM run, copy the MSFT handover below into the VM's `state.json`**
(or run with the position recorded as PROTECTED — see the handover),
so the VM copy knows about the open short and does not re-enter MSFT.

Example cron (ET, VM local time):

```
*/5 10-15 * * 1-5 cd /opt/marv-bot/muse1-alpaca && venv/bin/python bot.py --paper
```

## HANDOVER — open position (as of 2026-11-05 close)

The Muse VM copy already has ONE open paper position on the shared account:

- **MSFT SHORT x17, entry $537.05, filled avg $537.05**
- Hard stop **$539.83** via a REAL broker order
  `9a5b7fb5-ada0-4c34-b143-43411cca822b` (BUY STOP, GTC, ACCEPTED) —
  placed by the parent on 2026-11-05. **Do NOT cancel or replace it.**
  Virtual trail stop **$534.44** (peak $533.05, +1.44R at the close;
  after-hours the peak improved to ~$530.36, trail ~$531.75).
- Unrealized at close: +$44.88 (+0.95R). Equity $51,380.49.
- The 15:45 ET day flatten did NOT happen on 2026-11-05 (bot is run-once),
  so this day trade is carrying overnight. Owner chose "leave it alone".

In v0.2.0 this position is recorded in `state.json` as PROTECTED: the
bot monitors it only, never cancels/replaces its stop, and it is exempt
from auto-flatten. Whoever runs the bot next owns monitoring it: if the
virtual trail/stop triggers, the bot reports MANUAL ACTION REQUIRED
(the stop order is parent-placed). Do NOT ignore it.

Also recorded in `state.json`: TSLA short x37 closed 2026-11-05 at its
hard stop, realized −$81.67 (−1.31R) — that day's realized P&L, so the
2% daily loss cap is seeded correctly on the next run.

## DO NOT run both copies on the same paper account

The Muse VM copy and this VM copy must NOT both scan/trade the same Alpaca
paper account — and in v0.2.0 the VM copy must use its OWN separate paper
account anyway (see Keys above). Each copy counts its own max-5-open and
max-5-new/day, so two copies on one account can double-trade and break
the limits. Pick one owner for the account. While the VM copy runs, the
Muse VM copy stays stopped.

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
- **Rollout: ONE expert log-only — `raschke_soup`.** All other experts
  are off (`rollout.live: []`) until they pass a pre-registered
  backtest. No expert places real orders yet; new entries are bracket
  orders only when an expert is moved to live. The backtest with
  realistic costs is still owed.
- The bot is UNVALIDATED: no backtest has run on it yet. A pre-registered
  OOS backtest is owed (see the news-dip spec in the goal files), and a
  paper forward test is the real gate — not the backtest.

## Monitoring

The Muse VM ran a 30-min monitor on 2026-11-05 (positions, equity, SPY
latest trade, virtual trail/stop enforcement). That monitor is now
disabled. The VM needs its own equivalent: every 30 min, read positions,
enforce the virtual trail/stop (and the bracket legs for new positions),
report SPY price/side, and flatten day/scalp at 15:50 ET. Note v0.2.0
enforces exits inside every `--paper` run (breakeven, trail, max hold,
flatten), so the monitor is a second pair of eyes, not the only exit.
