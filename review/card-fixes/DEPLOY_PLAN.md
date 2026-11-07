# Deploy plan (do not run as part of this review)

This branch is a review diff. Do not merge it from here, do not copy it onto the running host, and do not restart anything while reviewing it.

The steps below are the later, separately approved rollout. They copy specific files. They do not run `deploy/install.sh`, do not `git fetch` / `checkout -f` / `reset` / `clean` / `stash`, do not use the root deploy key, and do not start or stop units.

## What the updater actually resets

`deploy/vm_link.py` `git()` always runs with `-C /opt/marv-link`. The `fetch` and `reset --hard origin/$VM_LINK_BRANCH` (default branch name `vm-link`) replace that link checkout only. They do not replace `/opt/marv-bot`.

`/opt/marv-bot` is replaced only when `install.sh` runs. `vm_link.py` runs that script for an `update` job. `install.sh` does `git checkout -f -B "$BRANCH" "origin/$BRANCH"` with `BRANCH` defaulting to `claude/analysis-ak180w`. That would discard tracked dirty work, including `alpacabot/data.py` and `alpacabot/paper.py`, and it would `systemctl enable --now` and restart services. Leave `marv-vmlink.timer` running. Do not delete or edit its jobs. If any pending job's command line is `update`, stop this rollout before copying bot files. Do not clear the job.

`/opt/marv-feed` is not a remote and is not checked out by either command. Feed files survive `install.sh`. They still have to be copied by hand or the bot-tree patch never reaches the service entrypoints, which are `/opt/marv-feed/run_marv.py` and `/opt/marv-feed/run_dailypicks.py`.

If a later `update` job runs before this commit is what `origin/claude/analysis-ak180w` points at, the bot-tree files revert and the feed copies remain. Pause still applies through the feed fallback. The append-only audit is imported from `marv.decision_audit`; if that module is gone, the feed logs the failure and keeps the previous prior-stake reader. Copy both trees together, and do not use `install.sh` to deliver them.

## Backup

Code files only. Do not copy `.env`, `state/`, pick ledgers, track files, or bet-card logs into the backup, and do not `chown -R` either tree.

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BK=/var/backups/marv-card-fixes-$STAMP
mkdir -m 700 "$BK"
for f in marv_lane_pause.py marv_ncaab_card.py run_marv.py run_marv_svc.py; do
  cp -a "/opt/marv-feed/$f" "$BK/feed-$f"
done
for f in marv/betcard.py marv/dailypicks.py marv/main.py deploy/marv-health.sh; do
  cp -a "/opt/marv-bot/$f" "$BK/bot-$(printf '%s' "$f" | tr / _)"
done
for f in marv/lane_pause.py marv/decision_audit.py; do
  if [ -e "/opt/marv-bot/$f" ]; then cp -a "/opt/marv-bot/$f" "$BK/bot-$(printf '%s' "$f" | tr / _)"
  else printf 'absent\n' > "$BK/bot-$(printf '%s' "$f" | tr / _).absent"; fi
done
sha256sum "$BK"/* > "$BK/SHA256SUMS"
```

## Copy

`SRC` is a checkout of this commit made somewhere other than `/opt/marv-bot`. For each existing destination, keep that file's user and group. New files take the user and group of their directory. Modes: `0644`, except `run_marv_svc.py` which stays `0755`.

Feed (same filenames the services already import; this is not a new import path):

- `review/card-fixes/overlays/feed/marv_lane_pause.py` → `/opt/marv-feed/marv_lane_pause.py`
- `review/card-fixes/overlays/feed/marv_ncaab_card.py` → `/opt/marv-feed/marv_ncaab_card.py`
- `review/card-fixes/overlays/feed/run_marv.py` → `/opt/marv-feed/run_marv.py`
- `review/card-fixes/overlays/feed/run_marv_svc.py` → `/opt/marv-feed/run_marv_svc.py`

Bot tree:

- `marv/lane_pause.py`
- `marv/decision_audit.py`
- `marv/betcard.py`
- `marv/dailypicks.py`
- `marv/main.py`
- `deploy/marv-health.sh`
- `tests/test_lane_pause.py`
- `tests/test_decision_audit.py`

Do not copy `alpacabot/`, `fxbot/`, `.env`, or anything under `state/`. Do not touch the prop-shadow dayblock.

`marv-health.timer` is already active. The next timer tick will execute whatever `deploy/marv-health.sh` is on disk. Copy that script only when that tick is an accepted smoke. Do not `systemctl start marv-health`. A healthy check sends no message. A non-integer uptime or timestamp exits 1 and does not send. Do not restart `marv-bot`, `marv-dailypicks`, `marv-edges`, `marv-live`, or `marv-bridge` in this rollout; those processes keep the old code until a separately approved restart.

## Smoke

No provider calls, no Telegram, no `python -m marv run`.

```bash
bash -n /opt/marv-bot/deploy/marv-health.sh
cd /opt/marv-bot && sudo -u marvbot .venv/bin/python -m unittest tests.test_lane_pause tests.test_decision_audit
```

Do not run `unittest discover` on the host. Other tests write pause flags that a live process would honor. The two modules above use temporary directories only.

Optional mocked health run, still not the real unit: point `PATH` at stand-ins for `systemctl`, `journalctl`, `df`, and `runuser`, set `MARV_HEALTH_DIR` and `MARV_HEALTH_UPTIME_FILE` to temporary files, and run `bash deploy/marv-health.sh`. The real timer is the first live execution.

## Rollback

Copy the backup files back to the same paths, with the saved owner and mode. If a `*.absent` marker exists, remove the new file instead of restoring one. Do not `git reset`. Do not delete ledgers. Do not restart units unless a restart was already performed; another restart is its own approval.
