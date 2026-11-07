# Claude Prop 1 rollout: shadow on, sends off

Review only. Nothing here has been run on the VM. This branch does not change `deploy/install.sh` or `deploy/vm_link.py`. There is no deploy pin in the repo. Changing the updater so it cannot revert a checkout needs a separate explicit approval. Do not treat this document as that approval.

## Flag

`LIVE_PROPS_ENGINE` stays independent and defaults to **on**.

`LIVE_PROPS_SEND`:

| Value | Prop Telegram | Recommendation ledger | Prop disk output |
|---|---|---|---|
| unset | on (historical default) | written (`recs.json`, grading) | shadow plus the on-mode files below |
| `1`, `on`, `true`, `yes` (trimmed, any case) | on | written | same |
| `0`, `off`, `false`, `no`, blank, or anything else | off, fail closed | not read or written | see residual files |

`python -m marv live-props scan --dry-run` stays print-only for either value: no Telegram, no `shadow.jsonl`, no ledger. `live-props report` only prints. It does not send and does not write.

On mode still writes `state/live_props/pregame_lines.json`, still lets `LIVE_PROPS_LOG` write `state/live_props_log.json`, and still stores `prop_scanned` / `prop_usage` / `prop_pregame` / `prop_recs` on `state/live_state.json`.

## What off mode persists

While `LIVE_PROPS_SEND` is off, including when `LIVE_PROPS_ENGINE=on` and `LIVE_PROPS_LOG=on`:

Prop workflow, service path (`marv-live`):

- `state/live_props/shadow.jsonl` — priced props. This is the intended dataset.
- `state/live_props/credits.json` — **unresolved.** The daily Odds API cap (`LIVE_PROPS_MAX_CREDITS`, default 600) is charged before each fetch and has to survive a `marv-live` restart. An in-memory counter would reset on restart and could spend past the cap. This file is not a recommendation ledger. The strict "only `shadow.jsonl`" requirement is not met until a separately approved design keeps that cap without this write.

Not written in off mode:

- `recs.json` (create, dedup, grade)
- `pregame_lines.json` (anchors are kept on the engine object for the life of the process; a restart drops them; `props_priced_<sport>.json` from the daily props run is still read)
- `live_props_log.json` (stage-1 logger is skipped even if `LIVE_PROPS_LOG=on`)
- prop keys on `live_state.json`: `prop_scanned`, `prop_usage`, `prop_pregame`, `prop_recs`, `props_recorded` (dedup and usage are in process memory)

`live-props scan` without `--dry-run` appends `shadow.jsonl` only. It does not go through the service budget helper, so it does not update `credits.json`. That is the existing CLI behavior. `--dry-run` writes no files.

Non-prop background, left alone:

- `state/live_state.json` is still saved every live tick for moneyline and total bookkeeping (`seen`, `reported`, `alerted`, `final`). Off mode must not add prop keys to it.
- Moneyline and total Telegram alerts use a different callback and still send.
- A failed prop break is remembered only in process memory, so that process does not refetch it. A restart can scan that break again. The credit cap still applies.

## Updater, and why this branch does not change it

`/opt/marv-bot` is one tree shared by the Marv timers and the long-running services. `marv-vmlink.timer` runs `deploy/vm_link.py`. Its `update` job runs `bash /opt/marv-bot/deploy/install.sh`.

Today's `install.sh` (unchanged on this branch):

- `BRANCH` defaults to `claude/analysis-ak180w`.
- If `/root/.ssh/marv_link` exists, it points `origin` at the SSH remote and fetches with that key.
- `git checkout -f -B "$BRANCH" "origin/$BRANCH"` discards tracked local edits and moves HEAD to that remote tip.
- It enables the shared Marv units.
- It restarts `marv-bridge`, `marv-live`, and `marv-edges` when betting files differ. This branch's `marv/*.py` changes count as betting files.
- It always restarts `marv-alpaca-paper` and `marv-fx-paper`, and enables the swing timers, even when the diff is documentation only.

`deploy/vm-link.sh` hardcodes the same default branch and curls `install.sh` from it. Do not run that script. Do not use `/root/.ssh/marv_link`.

Any code change that makes `update` refuse to force-checkout, or that otherwise protects a pinned SHA, is a separate change. It needs its own explicit approval. This rollout does not add that behavior, and it must not be described as if a pin already exists.

Until that separate change is approved and deployed, the way to keep `update` from reverting this tree is operational: stop `marv-vmlink.timer` and `marv-vmlink.service` before the checkout, and leave them stopped. That pauses vm-link jobs. It does not stop `marv-bot`, `marv-live`, `marv-edges`, `marv-bridge`, soccer, health, Alpaca, forex, or swing. Re-enabling the timer with today's `vm_link.py` allows the next `update` job to force-checkout `origin/claude/analysis-ak180w` and to restart Alpaca and forex.

## Facts to confirm on the VM before any change

This workspace cannot see the VM. Confirm each item. Stop if one disagrees.

1. `git -C /opt/marv-bot rev-parse HEAD` is `e7d1f50c048735a2348e1f70ac1fbc538640dcbc` and the branch is `claude/analysis-ak180w`.
2. `git status --porcelain`. In particular, whether any path this checkout would change is already dirty or untracked. From `e7d1f50` that list includes `marv/main.py`, `marv/live_monitor.py`, `marv/live_prop_engine.py` (new), the prop tests, `CLAUDE.md`, `.env.example`, and this document. `deploy/install.sh` and `deploy/vm_link.py` are not in this branch's diff against `3cb98ec`, but they do differ from `e7d1f50` because later commits on the way to `3cb98ec` touched `vm_link.py`. Check the real `git diff --name-only HEAD <pin>` on the VM.
3. `git remote -v` and `git rev-parse origin/claude/analysis-ak180w`. In this workspace's cached ref that tip was `0a64d9c8bdc00159db900786ac672731f951da0f` (documentation only past `e7d1f50`). The cache can be stale. Do not fetch while confirming.
4. `systemctl is-active` and `ActiveEnterTimestamp` for `marv-live`, `marv-vmlink.timer`, `marv-vmlink.service`, and every other `marv-*` unit.
5. Pending jobs under `/opt/marv-link/jobs`, especially any whose command is `update`.
6. `systemctl cat marv-live` still shows `EnvironmentFile=/opt/marv-bot/.env` and `python -m marv live`.
7. Presence only of `LIVE_PROPS_ENGINE`, `LIVE_PROPS_SEND`, and whether `ODDS_API_KEY` is non-empty. Do not print the key or the rest of `.env`. The engine prices only when the odds key is set.
8. The tar `/home/moonrider1968/backups/prop1-deploy-20261106-1946/marv-bot-full.tar` (mode 600) stays where it is. It is older than dirty edits made after 2026-11-06 19:46. Do not upload it and do not untar it over `/opt/marv-bot`.

## Preserve dirty and untracked state

Take a new local snapshot before changing the tree. Mode 700 directory, mode 600 files. Exclude `.env` from the archive.

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

Stop the updater first if an `update` job may be pending:

```bash
sudo systemctl stop marv-vmlink.timer marv-vmlink.service
```

Fetch the reviewed commit over anonymous HTTPS. Do not set `GIT_SSH_COMMAND`. Do not use `/root/.ssh/marv_link`. Do not change `origin`. Do not run `install.sh`.

```bash
git -C /opt/marv-bot fetch https://github.com/marvy1968/marvy1968.git \
  cursor/prop1-send-off-gate-d7c4
git -C /opt/marv-bot rev-parse FETCH_HEAD
```

`FETCH_HEAD` must equal the reviewed SHA (the tip of this branch, both commits). If any porcelain path is also in `git diff --name-only HEAD FETCH_HEAD`, stop. Do not `git checkout -f`, do not stash those paths away, and do not continue.

When there is no overlap:

```bash
git -C /opt/marv-bot checkout --detach FETCH_HEAD
```

`--detach` without `-f` leaves unrelated dirty and untracked files in place and refuses if a tracked edit would be overwritten. After it succeeds, `git rev-parse HEAD` is the reviewed SHA and `git status --porcelain` still shows the unrelated dirty paths.

## Flags, tests, one restart

1. Edit `/opt/marv-bot/.env` so the only lines added or replaced are `LIVE_PROPS_ENGINE=on` and `LIVE_PROPS_SEND=off`. Keep mode 600 and owner `marvbot`. Do not print the file. Unset `LIVE_PROPS_SEND` still means **on**.
2. Compile and run the focused tests as `marvbot`. No Telegram and no orders:

   ```bash
   sudo -u marvbot /opt/marv-bot/.venv/bin/python -m py_compile \
     marv/live_prop_engine.py marv/live_monitor.py marv/main.py
   sudo -u marvbot /opt/marv-bot/.venv/bin/python -m unittest \
     tests.test_live_prop_engine tests.test_live_monitor tests.test_live_props
   ```

3. Restart **only** `marv-live`:

   ```bash
   sudo systemctl restart marv-live.service
   ```

   Compare `ActiveEnterTimestamp` for every other `marv-*` unit with the list from the confirm step. Those timestamps stay the same. Do not restart Alpaca, forex, bridge, edges, or the swing timers.
4. Leave `marv-vmlink.timer` stopped until a separately approved updater change is in place. Starting it again with today's `install.sh` can force-checkout the default branch and restart Alpaca and forex.

## Verify

After the `marv-live` restart, print only the two flags from the service process:

```bash
pid=$(systemctl show -p MainPID --value marv-live)
sudo tr '\0' '\n' < /proc/$pid/environ | grep -E '^(LIVE_PROPS_ENGINE|LIVE_PROPS_SEND)='
```

Expected: `LIVE_PROPS_ENGINE=on` and `LIVE_PROPS_SEND=off`.

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

Expected: engine `on`, send `off`, `send_enabled False`, `odds_key_set True`. If the odds key is empty, the engine does not price.

`git rev-parse HEAD` equals the reviewed SHA.

On a later live break (do not invent one):

- `shadow.jsonl` gains rows.
- `credits.json` may change. That is the unresolved budget file.
- `recs.json`, `pregame_lines.json`, and `live_props_log.json` do not appear or change because of the prop engine.
- `live_state.json` may change and must not gain `prop_scanned`, `prop_usage`, `prop_pregame`, `prop_recs`, or `props_recorded`.
- `journalctl -u marv-live` shows `live props: Telegram suppressed` when a prop break is dropped. Moneyline and total alerts may still send.

Optional dry-run, which calls the odds host and writes nothing: `sudo -u marvbot /opt/marv-bot/.venv/bin/python -m marv live-props scan --sport nfl --dry-run`.

## Rollback

1. `sudo systemctl stop marv-live` so it cannot load a half-reverted tree. Leave every other unit stopped or running as it already is. Do not start `marv-vmlink` if it was stopped for this rollout.
2. `git checkout --detach e7d1f50c048735a2348e1f70ac1fbc538640dcbc` from `/opt/marv-bot`. No `-f`. If git refuses because a dirty path overlaps, restore those paths from `$DEST` and leave unrelated dirty files as they are.
3. The two `.env` lines can stay (`LIVE_PROPS_SEND` is unused on `e7d1f50`) or be removed with the same key-only edit. Do not restore `.env` from the full tar.
4. `sudo systemctl start marv-live`. Confirm the other units' `ActiveEnterTimestamp` values did not move.
5. Use the 2026-11-06 full tar only if the worktree itself is unreadable, and then only to recover specific paths in a separate directory. Do not untar it onto `/opt/marv-bot`.

## What this review already ran

On the review host, not on the VM. `deploy/install.sh` was not executed. The updater files in this branch match the parent `3cb98ec` (the pin edit from the first commit is removed). Focused tests:

```text
python3 -m unittest tests.test_live_prop_engine tests.test_live_monitor tests.test_live_props
Ran 36 tests in 0.088s
OK
```

The full `unittest discover -s tests` suite was not run. No Telegram request, no odds order, and no VM access were made from this review.
