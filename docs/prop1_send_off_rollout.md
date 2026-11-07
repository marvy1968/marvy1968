# Claude Prop 1 rollout: shadow on, sends off

Review only. Nothing in this document has been run on the VM. Do not merge this branch, do not restart services, and do not run `deploy/install.sh` or `deploy/vm-link.sh` as part of reading it.

Pin one commit: the tip of `cursor/prop1-send-off-gate-d7c4` after review. Compare `git rev-parse HEAD` on the VM to the full SHA in the review message. Do not pin a floating branch name.

## What the code does

`LIVE_PROPS_ENGINE` stays independent and defaults to **on**. The engine still prices live props.

`LIVE_PROPS_SEND`:

| Value | Telegram prop sends | `recs.json` and grading | `shadow.jsonl` |
|---|---|---|---|
| unset | on (historical default) | written | written |
| `1`, `on`, `true`, `yes` (trimmed, any case) | on | written | written |
| `0`, `off`, `false`, `no`, blank, or anything else | off, fail closed | not read or written | written |

`python -m marv live-props scan --dry-run` stays print-only for either flag value: no Telegram, no `shadow.jsonl`, no ledger. `live-props report` only prints.

The gate is in one place and checked again at each side effect:

- `live_props_send_enabled()` is false unless the value is an explicit on token.
- `record_recs`, `grade_game`, and `save_recs` return without touching `recs.json` when the flag is off, so a caller that forgets the check still cannot write the ledger.
- `deliver_prop_message()` does not call the sender when the flag is off.
- `marv-live` passes `bind_prop_send(send)` into the prop engine only. Moneyline and total alerts keep the original callback.
- `cmd_live_props` writes shadow on a real scan, and calls `send_message` only through `deliver_prop_message` after the flag check.

While sends are off the engine still updates two operational files, because they are inputs to pricing rather than bets:

- `state/live_props/credits.json` — Odds API daily budget.
- `state/live_props/pregame_lines.json` — pregame anchors captured before kickoff.

`state/live_state.json` still records `prop_scanned` (one fetch per break) and `prop_usage` (the usage gate). It does not record `prop_recs` while sends are off. The stage-1 NFL logger (`LIVE_PROPS_LOG`, default on, `marv/live_props.py`) still writes `state/live_props_log.json` and does not send Telegram. Leave it unless a separate review turns it off.

A break that raises is marked scanned before the call, so that break is not retried. That avoids a credit loop when the odds host is down. The ledger is still not written.

## Why `install.sh` can revert a deploy

`/opt/marv-bot` is one tree shared by the Marv timers and the long-running services. `marv-vmlink.timer` runs `deploy/vm_link.py`. Its `update` job runs `bash /opt/marv-bot/deploy/install.sh`.

On the script that is on the VM today (the pin check below is only in this branch):

- `BRANCH` defaults to `claude/analysis-ak180w` (`deploy/install.sh`).
- If `/root/.ssh/marv_link` exists, the script points `origin` at `git@github.com:marvy1968/marvy1968.git` and fetches with that key.
- `git checkout -f -B "$BRANCH" "origin/$BRANCH"` discards tracked local edits and moves HEAD to that remote tip.
- It then enables the shared Marv units, restarts `marv-bridge`, `marv-live`, and `marv-edges` when betting files differ, and always restarts `marv-alpaca-paper` and `marv-fx-paper` and enables the swing timers.

`deploy/vm_link.py` is on the “do not restart betting services” allowlist, but this branch also changes `marv/live_monitor.py`, `marv/live_prop_engine.py`, and `marv/main.py`. An `update` job would treat that as betting code and restart bridge, live, and edges, and it would still restart the Alpaca and forex services.

`deploy/vm-link.sh` hardcodes the same default branch and curls `install.sh` from it. Do not run that script.

This branch refuses `update`, and refuses `install.sh` before apt, fetch, checkout, or any restart, when either of these is set:

- environment `MARV_DEPLOY_PIN` (any non-empty value), or
- a file at `MARV_DEPLOY_PIN_FILE` (default `/etc/marv-deploy-pin`), even if the file is empty.

Other vm-link jobs (`tests`, `status`, `logs`, `show`, `marv`, …) still parse. The refusal text does not include the pin body. With neither pin set, `update` behaves as it does today. The VM’s current `vm_link.py` and `install.sh` ignore the pin until this commit is the code on disk. Stopping the timer is what protects the tree before that checkout.

`marv-vmlink.service` is installed by `vm-link.sh` under `/etc/systemd/system/`. `install.sh` does not ship that unit. A drop-in under `marv-vmlink.service.d/` stays put. Do not rely on the drop-in until the new `vm_link.py` is on disk.

## Facts to confirm on the VM before any change

This workspace cannot see the VM. The notes below are the operator’s report plus this repo at review time. Confirm each one. Stop if any of them disagree.

1. `git -C /opt/marv-bot rev-parse HEAD` is `e7d1f50c048735a2348e1f70ac1fbc538640dcbc` and the branch is `claude/analysis-ak180w`.
2. `git status --porcelain` (dirty and untracked). In particular, whether any of these paths are modified or untracked: `marv/main.py`, `marv/live_monitor.py`, `marv/live_prop_engine.py`, `tests/test_live_prop_engine.py`, `tests/test_live_monitor.py`, `deploy/install.sh`, `deploy/vm_link.py`, `CLAUDE.md`, `.env.example`, `docs/prop1_send_off_rollout.md`.
3. `git remote -v` and `git rev-parse origin/claude/analysis-ak180w`. In this workspace’s cached ref that tip was `0a64d9c8bdc00159db900786ac672731f951da0f` (two documentation files after `e7d1f50`, prop engine not included). The cache can be stale. Do not fetch while confirming.
4. `systemctl is-active` and `systemctl show -p ActiveEnterTimestamp` for `marv-live`, `marv-vmlink.timer`, `marv-vmlink.service`, and every other `marv-*` unit. Record the list. The later restart check compares these timestamps.
5. Pending vm-link jobs: `ls /opt/marv-link/jobs`. Note any job whose first non-comment line is `update`.
6. Whether `/etc/marv-deploy-pin` or a `marv-vmlink.service.d` drop-in already exists.
7. `marv-live.service` still has `EnvironmentFile=/opt/marv-bot/.env` and `ExecStart=... python -m marv live`. Confirm with `systemctl cat marv-live`.
8. Presence only, not values, of `LIVE_PROPS_ENGINE`, `LIVE_PROPS_SEND`, and `ODDS_API_KEY` in the environment the service will see. The engine prices only when the odds key is non-empty. Print booleans and the two prop flags, never the key and never the rest of `.env`.
9. The full backup `/home/moonrider1968/backups/prop1-deploy-20261106-1946/marv-bot-full.tar` (mode 600) stays where it is. It is older than any dirty edits made after 2026-11-06 19:46. Do not upload it and do not untar it over `/opt/marv-bot`.

## Preserve dirty and untracked state

Take a new local snapshot before changing the tree. Keep it on the VM, mode 700 directory and mode 600 files. Exclude `.env` from the archive (the existing tar already holds private config; a second copy should not be made casually).

```bash
STAMP=prop1-predeploy-$(date -u +%Y%m%d-%H%M%S)
DEST=/home/moonrider1968/backups/$STAMP
mkdir -m 700 "$DEST"
cd /opt/marv-bot
git status --porcelain > "$DEST/status.txt"
git diff > "$DEST/tracked.diff"
git diff --cached > "$DEST/staged.diff"
git ls-files --others --exclude-standard | grep -v -x '.env' > "$DEST/untracked.txt" || true
tar -C /opt/marv-bot -czf "$DEST/untracked.tgz" -T "$DEST/untracked.txt"
chmod 600 "$DEST"/*
```

Fetch the reviewed commit over anonymous HTTPS. Do not set `GIT_SSH_COMMAND`. Do not use `/root/.ssh/marv_link`. Do not change `origin`.

```bash
git -C /opt/marv-bot fetch https://github.com/marvy1968/marvy1968.git \
  cursor/prop1-send-off-gate-d7c4
git -C /opt/marv-bot rev-parse FETCH_HEAD
```

`FETCH_HEAD` must equal the reviewed SHA. If it does not, stop.

Build the file list that checkout would change (`git diff --name-only HEAD FETCH_HEAD`). If `git status --porcelain` names any of those paths, stop. Do not `git checkout -f`, do not stash those paths away, and do not continue. Reconcile the overlap by hand using the snapshot, then start this section again.

When there is no overlap:

```bash
git -C /opt/marv-bot checkout --detach FETCH_HEAD
```

`--detach` without `-f` leaves unrelated dirty and untracked files in place and refuses if a tracked edit would be overwritten. After it succeeds, `git rev-parse HEAD` is the pin SHA and `git status --porcelain` still shows the unrelated dirty paths from `status.txt`.

## Pin, flags, tests, one restart

Do this only after the timer is stopped and HEAD is the reviewed SHA.

1. Stop the updater without stopping the bots. `systemctl stop marv-vmlink.timer marv-vmlink.service` pauses vm-link jobs. Leave `marv-bot`, `marv-live`, `marv-edges`, `marv-bridge`, soccer, health, Alpaca, forex, and swing units running. Do this **before** the checkout if an `update` job might be pending; a stop after checkout is too late if a job was already in `install.sh`.
2. Edit `/opt/marv-bot/.env` so the only lines added or replaced are `LIVE_PROPS_ENGINE=on` and `LIVE_PROPS_SEND=off`. Keep mode 600 and owner `marvbot`. Do not print the file.
3. Write the reviewed SHA to `/etc/marv-deploy-pin` (mode 644, root). Add `/etc/systemd/system/marv-vmlink.service.d/pin.conf`:

   ```
   [Service]
   Environment=MARV_DEPLOY_PIN=<40-char SHA>
   Environment=MARV_DEPLOY_PIN_FILE=/etc/marv-deploy-pin
   ```

   `systemctl daemon-reload`. Leave the timer stopped until the checks below pass.
4. Compile and run the focused tests as `marvbot`, from `/opt/marv-bot`, with the venv. No Telegram, no orders:

   ```bash
   sudo -u marvbot /opt/marv-bot/.venv/bin/python -m py_compile \
     marv/live_prop_engine.py marv/live_monitor.py marv/main.py deploy/vm_link.py
   sudo -u marvbot /opt/marv-bot/.venv/bin/python -m unittest \
     tests.test_live_prop_engine tests.test_live_monitor tests.test_vm_link_pin tests.test_live_props
   ```

5. Restart **only** `marv-live`:

   ```bash
   sudo systemctl restart marv-live.service
   ```

   Compare `ActiveEnterTimestamp` for every other `marv-*` unit with the list from the confirm step. Those timestamps stay the same.
6. Start `marv-vmlink.timer` again only after the pin file, the drop-in, and the new `vm_link.py` are all in place. The next `update` job is refused. Other jobs still run. Alpaca, forex, and the betting timers are not restarted by that refusal.

## Verify effective flags and shadow-only output

Run these after the `marv-live` restart. Print only the two flags.

```bash
pid=$(systemctl show -p MainPID --value marv-live)
sudo tr '\0' '\n' < /proc/$pid/environ | grep -E '^(LIVE_PROPS_ENGINE|LIVE_PROPS_SEND)='
```

Expected lines: `LIVE_PROPS_ENGINE=on` and `LIVE_PROPS_SEND=off`.

From a clean process that loads `.env` the same way the code does, print the gate and a boolean for the odds key. Do not print the key:

```bash
sudo -u marvbot /opt/marv-bot/.venv/bin/python - <<'PY'
import os
from pathlib import Path
from marv.config import load_dotenv
from marv.live_prop_engine import live_props_send_enabled
load_dotenv(Path("/opt/marv-bot/.env"))
print("LIVE_PROPS_ENGINE", os.environ.get("LIVE_PROPS_ENGINE", "<unset>"))
print("LIVE_PROPS_SEND", os.environ.get("LIVE_PROPS_SEND", "<unset>"))
print("send_enabled", live_props_send_enabled())
print("odds_key_set", bool(os.environ.get("ODDS_API_KEY", "").strip()))
PY
```

Expected: engine `on`, send `off`, `send_enabled False`, `odds_key_set True`. If the odds key is empty the engine does not price, which is a different state from “engine on, sends off”.

`git -C /opt/marv-bot rev-parse HEAD` equals the pin file and the reviewed SHA.

On a later live break (do not invent one and do not place orders):

- `state/live_props/shadow.jsonl` gains rows.
- `state/live_props/recs.json`, `state/picks.json`, and `state/live_alerts.json` keep their previous mtimes. `credits.json` and `pregame_lines.json` may change. `live_props_log.json` may change while `LIVE_PROPS_LOG` stays on; that file is the stage-1 recorder, not a Telegram send.
- `journalctl -u marv-live --since '<restart time>'` shows `live props: Telegram suppressed` when a prop break is dropped, and shows no prop card body going out. Moneyline and total alerts are unchanged and may still send.

Optional read-only CLI check, which does call the odds host and must not be used as a send test: `sudo -u marvbot /opt/marv-bot/.venv/bin/python -m marv live-props scan --sport nfl --dry-run`. Dry-run writes nothing.

## Rollback

Leave `/etc/marv-deploy-pin` and the drop-in in place during rollback. They only block `update`. Removing them re-opens force-checkout to `origin/claude/analysis-ak180w`.

1. `sudo systemctl stop marv-vmlink.timer marv-vmlink.service` if it was started.
2. `sudo systemctl stop marv-live` so it cannot load a half-reverted tree.
3. Return the tree with `git checkout --detach e7d1f50c048735a2348e1f70ac1fbc538640dcbc` from `/opt/marv-bot`. Same rule as the rollout: no `-f`. If git refuses because a dirty path overlaps, restore those paths from `$DEST` and leave unrelated dirty files as they are.
4. The two `.env` lines can stay (`LIVE_PROPS_SEND` is unused on `e7d1f50`) or be removed with the same key-only edit. Do not restore `.env` from the full tar.
5. `sudo systemctl start marv-live`. Confirm the other units’ `ActiveEnterTimestamp` values did not move.
6. Use the 2026-11-06 full tar only if the worktree itself is unreadable, and then only to recover specific code paths in a separate directory. Do not untar it onto `/opt/marv-bot`.

## What this review already ran

On the review host, not on the VM: `bash -n deploy/install.sh` (syntax only; the script was not executed), and

```text
python3 -m unittest tests.test_live_prop_engine tests.test_live_monitor tests.test_vm_link_pin tests.test_live_props
Ran 36 tests in 0.082s
OK
```

The full `unittest discover -s tests` suite was not run. No Telegram request, no odds order, and no VM access were made from this review.
