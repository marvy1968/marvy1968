# DEPLOY — muse1/alpaca on the VM

Paper only. Live trading is always blocked in `bot.py` (`--live` exits 2).

## What this is

`muse1/alpaca` — a paper stock bot: day trade + swing trade + scalp, 10
experts, hedge-fund overlay (vol-scaled sizing, portfolio limits, no fixed
1R TP, signal-decay exit). Built 2026-11-05 on Muse's VM, deployed once
there the same day. This push moves it to the VM so it can run there.

**Version 0.3.0 (2026-11-05) — Claude fix list, round two, applied:**
no-target signals submit entry+stop only (never a TP-at-entry bracket);
swing uses its own 2R TP (day 2.5R no longer leaks in); Brooks/Dalton
submit exactly the target they checked; a failed stop replace
re-submits a standalone stop, verifies it, and blocks new trades while
any position is unprotected; the duplicate check sees ALL broker
positions and adopts unknown ones (so the VM copy adopts the MSFT short
from the broker on first run); the VM schedule runs through 15:50 ET.
See README.md "v0.3.0 — what changed" for the full list.

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
python3 test_round2.py             # round-two checks (items 1-5, 7a), no keys, no network
```

## Running it — it is run-once, it needs a schedule

`bot.py --paper` scans once and exits. It does NOT keep scanning. To trade
through the day, run it every 5 minutes inside the windows, e.g. a systemd
timer or cron:

- scalp + day sleeves: 10:00–15:55 ET — the schedule MUST run through
  15:50 ET, because the 15:50 run is what flattens day/scalp positions
  (round-two fix, item 7a: an earlier schedule that stopped at 15:45
  would never flatten — that is exactly what happened on 2026-11-05,
  when the MSFT day short carried overnight). The flatten itself is
  `flatten_et: "15:50"` in `config.yaml` (`sleeves.day`), and
  `manage_positions` closes day/scalp positions on any run at or after
  15:50 ET; on half-days the calendar moves the flatten to 13:00 ET.
- swing sleeve: daily bars, once a day is enough (it can carry over)

The bot takes a lock file (`bot.lock`) so overlapping runs cannot
double-trade; a second run while one holds the lock exits 0 without
trading. State (`state.json`, local only, never in git) is created on
the first `--paper` run — it starts empty on the VM.

Example cron (ET, VM local time). Hour `10-15` means 10:00 through
15:55 — the 15:50 and 15:55 runs are INSIDE this range, so the flatten
run happens. Do NOT narrow the hours to `10-14` or end the schedule
at 15:45: the day would end with no flatten run.

```
*/5 10-15 * * 1-5 cd /opt/marv-bot/muse1-alpaca && venv/bin/python bot.py --paper
```

Verify the schedule actually covers the flatten run (after the first
day, or by inspection):

```bash
# The cron line's hour field must include 15 (10-15), so runs happen
# at 15:50 and 15:55 ET. Check the installed crontab:
crontab -l | grep muse1-alpaca
# And confirm a 15:50 run flattened day/scalp: the run's output must
# contain "flatten 15:50 ET" (or "flatten 13:00 ET" on a half-day)
# for every day position that was open at 15:50.
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

In v0.3.0 this position is adopted AUTOMATICALLY from the broker on
the VM copy's first run — do NOT copy it into `state.json` by hand,
and do NOT commit `state.json` or any file containing the position.
Broker-reconciliation (`adopt_broker_positions` in `bot.py`) runs on
every `--paper` start: it reads ALL broker positions, and any position
`state.json` does not know about is adopted with the broker's own
entry price, as PROTECTED (monitor-only — the bot never cancels or
replaces its stop). For MSFT it also reads the open stop order
`9a5b7fb5 @ $539.83`, so the adopted record gets `one_r = $2.78` and
can actually be monitored (virtual trail/stop math runs on it).

**First-run seeding steps on the VM (do these, in order):**
1. Confirm the position is still open on the account the VM `.env`
   points at, and that the stop order `9a5b7fb5 @ $539.83` is still the
   only open order. If the VM `.env` points at muse1's OWN separate
   paper account (as required), the MSFT short will NOT be there —
   that is expected: the position lives on the shared account the Muse
   VM copy used, and the VM copy adopts it only when it runs against
   that same account. Do not force it onto a different account.
2. Run `venv/bin/python bot.py --dry-run` first (no orders). The
   output must contain a note like:
   `MSFT: adopted UNKNOWN broker position into state as PROTECTED
   (monitor-only, entry $537.05, open stop $539.83, 1R $2.78)`.
   If that line is missing, STOP — the VM copy does not see the
   position, and it may re-enter MSFT. Check the account/keys first.
3. Only then run `venv/bin/python bot.py --paper`. The adopted MSFT
   record is now in the VM's `state.json` (created by the run itself),
   and the bot monitors it only: if the virtual trail/stop triggers,
   the bot reports MANUAL ACTION REQUIRED (the stop order is
   parent-placed). Do NOT ignore it.

Also recorded in the Muse VM's `state.json` (local only, NOT on this
branch): TSLA short x37 closed 2026-11-05 at its hard stop, realized
−$81.67 (−1.31R) — that day's realized P&L, so the 2% daily loss cap
is seeded correctly on the next run. The VM copy starts with an empty
`state.json`, so its day-P&L seed starts at $0 — that is correct for
a fresh account, and the TSLA loss belongs to the old account's day.

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
  orders only when an expert is moved to live.
- The bot is UNVALIDATED: the pre-registered out-of-sample backtest of
  `raschke_soup` (v0.3.0, `backtest_muse1.py`) is in this push — and it
  FAILED (1,062 trades, PF 0.216, avg R −0.501; Train PF 0.227, Test PF
  0.194; random median PF 0.20/0.194). See README.md for the full
  result and why it loses. `raschke_soup` stays log-only. A paper
  forward test is the real gate — not the backtest.

## Monitoring

The Muse VM ran a 30-min monitor on 2026-11-05 (positions, equity, SPY
latest trade, virtual trail/stop enforcement). That monitor is now
disabled. The VM needs its own equivalent: every 30 min, read positions,
enforce the virtual trail/stop (and the bracket legs for new positions),
report SPY price/side, and flatten day/scalp at 15:50 ET. Note v0.3.0
enforces exits inside every `--paper` run (breakeven, trail, max hold,
flatten, and the unprotected-stop block), so the monitor is a second
pair of eyes, not the only exit.
