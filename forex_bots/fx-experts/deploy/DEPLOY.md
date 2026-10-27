# fx-experts: VM deployment plan (prepared, NOT installed)

Target: instance-stockbot, `/opt/fx-experts`, user moonrider1968.

Nothing existing is touched:
- no existing unit, cron entry, env file or directory is modified, restarted or stopped;
- the new units are named `fx-experts*`.

## Before installing (needs Marvin)
1. **Make a new OANDA *practice* API token** (OANDA hub → Manage API Access). Every OANDA token on the VM currently returns 401.
   - Regenerating the token invalidates the old one. The old ones already fail, so this changes nothing for the other bots unless Marvin also puts the new token into their env files. That is his call.
2. Recommended: in the OANDA hub, **create 5 practice sub-accounts**, one per system. The account is US (FIFO, no hedging), so systems sharing a pair in one account would otherwise block each other.
3. Approve the deploy.

## Install steps (run by the agent after approval)
1. Copy `fx-experts-bundle.tgz` to `/tmp` on the VM and untar it to `/tmp/fx-experts`.
2. Run `bash /tmp/fx-experts/deploy/install.sh`. It:
   - creates `/opt/fx-experts` and a venv;
   - creates `.env` (mode 600) from env.example;
   - runs the unit tests;
   - runs the read-only preflight (`python -m fxexp.check`): token, practice account IDs and spreads, with no orders;
   - does one `--dry-run` pass (computes orders, sends nothing);
   - installs the four unit files, **not enabled**.
3. Fill in `/opt/fx-experts/.env`:
   - the token;
   - the account ID or IDs;
   - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, copied from the existing scorecard env.
4. Enable: `sudo systemctl enable --now fx-experts.timer fx-experts-scorecard.timer`.

## What runs
| Unit | When | What |
|---|---|---|
| `fx-experts.timer` → `fx-experts.service` | every 5 min (no-op Fri 17:00 – Sun 17:00 NY) | one-shot runner: daily plan after 17:15 NY, arming/placing broker-side stop entries with SL/TP, daily stop trailing, Crabel flat at 16:50 NY |
| `fx-experts-scorecard.timer` | Mon-Fri 16:20 CT | send-only Telegram scorecard per system (day / week / total P&L, trades, win %, open) |

- **Logs:** `/opt/fx-experts/logs/runner.log` and `/opt/scorecards/logs/fx-experts.log`.
- **State:** `/opt/fx-experts/state/live_state.json`. If it is lost, broker-side stops still protect every trade.

## Safety
- `OANDA_ENV` must be `practice`. The client also refuses the live host unless `FX_ALLOW_LIVE=YES`, which is never set.
- Secrets are only in the mode-600 `.env`. The unit files contain no secrets.
- **Kill switch:** set `"no_new_entries": true` in config.json (existing trades keep their broker stops), or `sudo systemctl disable --now fx-experts.timer`.
