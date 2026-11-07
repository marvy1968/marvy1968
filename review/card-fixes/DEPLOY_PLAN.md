# Deploy plan

**Deployment is BLOCKED.** Do not copy these files onto `/opt/marv-bot` or `/opt/marv-feed`, do not restart units, and do not run `deploy/install.sh`.

A safe rollout has to make this commit the source of truth on the branch the host actually runs (`claude/analysis-ak180w` at `e7d1f50c` today) without discarding unrelated dirty work and without leaving the feed overlays as an untracked patch. This plan does not have that step.

## Why a file copy is not safe

`deploy/vm_link.py` `git()` uses `-C /opt/marv-link`. Its `fetch` and `reset --hard origin/$VM_LINK_BRANCH` (default name `vm-link`) replace that link checkout only.

`/opt/marv-bot` changes when `install.sh` runs. An `update` job on the link runner runs that script. `install.sh` does `git checkout -f -B "$BRANCH" "origin/$BRANCH"` (`BRANCH` defaults to `claude/analysis-ak180w`). That discards tracked dirty files, including `alpacabot/data.py` and `alpacabot/paper.py`, and it enables and restarts services.

Leaving the timer running does not remove that job. If an `update` job is already queued, or anyone enqueues one later, a hand copy of bot-tree files is reverted and the dirty alpaca edits are dropped. Do not delete the job and do not stop the timer from this review.

`/opt/marv-feed` has no remote. `install.sh` does not update it. The live card processes are started from feed wrappers (`run_marv.py`, `run_dailypicks.py`; `run_marv_svc.py` is the edges/bridge wrapper). Those files are not what a merge of this branch installs. Copying them by hand would be an untracked production patch, which this plan does not authorize.

Merging this PR into `claude/analysis-ak180w` is the durable source of truth for the files that live in the git tree (`marv/lane_pause.py`, `marv/decision_audit.py`, `marv/betcard.py`, `marv/dailypicks.py`, `marv/parlay.py`, `marv/main.py`, `deploy/marv-health.sh`). It does not, by itself, update a running interpreter or the feed directory. There is still no approved way to check that merge out on the host without `checkout -f` or a manual overlay copy. Until that exists, production deployment stays blocked.

## Further approval required

This review's approval is the code review and the scoped overlay fix only. It does not include merge, installers, updater changes, or restarts. No safe host deployment is available from this branch alone.

A later rollout needs separate human approval for each of these:

1. Merge this PR into `claude/analysis-ak180w` (the branch `install.sh` defaults to). This review does not merge.
2. A host update that keeps dirty tracked files. `install.sh` / `checkout -f` does not. Do not run it under the current approval.
3. How the feed entrypoints pick up the overlay that already lives in git at `review/card-fixes/overlays/feed/`. A hand copy onto `/opt/marv-feed` is an untracked patch and is not approved.
4. Restart or replacement of any long-running process (`marv-edges`, `marv-bridge`, `marv-live`), and the next `marv-health.timer` tick if `marv-health.sh` on disk has changed.

## Proposed options (not a procedure to run)

These are the smallest source-controlled choices. Neither is authorized now, and neither is safe to improvise on the host.

**Dirty files.** Before any future checkout of the merged branch, copy only `alpacabot/data.py` and `alpacabot/paper.py` to a directory outside the repo, then restore those two paths afterward if that checkout would replace them. Do not `reset`, `clean`, or `stash` the rest of the tree as a substitute. This still requires a separately approved checkout; it is not permission to run `install.sh`.

**Overlay durability.** The feed modules are already in this branch under `review/card-fixes/overlays/feed/`. After a merge, a future approved unit change can point the existing card drop-ins at those paths inside `/opt/marv-bot` (the deployed checkout) instead of a second untracked tree. The alternative is a later commit that moves the same modules under `marv/` so `python -m marv` is enough and the feed drop-in can be retired. Both change how a process is started, so both need their own approval and a restart decision. This PR does not add that unit change.

Until those approvals exist, do not treat the staged-test copy list as a production procedure.

## Entrypoints and freshness

Repo units (this commit) and the export's live drop-ins are not the same.

| Process | Repo `ExecStart` | Export drop-in (where recorded) | Lifetime | When new code is loaded |
|---|---|---|---|---|
| `marv-bot.service` | `python -m marv run --sport all` | `/opt/marv-feed/run_marv.py run --sport all` | oneshot | next process start |
| `marv-bot-late.service` | `python -m marv run --sport all --hours 6 --label "Late update"` | `run_marv.py` with the late args | oneshot | next process start |
| `marv-dailypicks.service` | `python -m marv.dailypicks` | `/opt/marv-feed/run_dailypicks.py` | oneshot | next process start |
| `marv-health.service` | `bash /opt/marv-bot/deploy/marv-health.sh` | same path | oneshot, timer already active | next timer exec reads the script |
| `marv-edges.service` | `python -m marv edges --watch` | not in the export metadata; `run_marv_svc.py` is the feed wrapper for edges/bridge | long-running, `Restart=always` | the process keeps imported modules |
| `marv-bridge.service` | `python -m marv serve --port 8787` | not in the export metadata; same wrapper comment | long-running | first import of `dailypicks` sticks for the process |
| `marv-live.service` | `python -m marv live --interval 120` | not recorded | long-running | imported modules stick |

Card selection runs in the oneshots (`cmd_run` calls `betcard.candidates`, `tag_candidates`, `gap_candidates`, and `select`; `dailypicks.collect` calls `engine_legs` then `build`; `parlay.all_legs` is the fallback board). A oneshot that has already started does not reload. Do not kill an in-flight oneshot.

**APPROVAL REQUIRED, not part of this review:** replacing `marv-edges`, `marv-bridge`, or `marv-live` if a long-running process must observe the new modules. The bridge answers `/card` from `dailypicks` imported at first use. Edges and live do not build the pregame card. Restarting them does not activate the oneshot path, and this review does not restart them.

**APPROVAL REQUIRED:** letting the next `marv-health.timer` tick run a replaced `marv-health.sh`. The timer is already active. A healthy run sends nothing. A bad timestamp exits 1 and does not send. Do not `systemctl start marv-health`.

Direct `python -m marv` and `python -m marv.dailypicks` use the shared modules (`betcard.*` and `dailypicks.build` / `_pool_ok` are marked and call `lane_pause`). They do not load the feed overlay unless the feed directory is on `sys.path` and something calls `apply()`. The feed wrappers do that for the service entrypoints. Both layers use the same predicate when `marv.lane_pause` imports.

## Staged tests (not the production tree)

Run this from a clean directory that is not `/opt/marv-bot` and not `/opt/marv-feed`. Do not put either production path on `PYTHONPATH`. Do not copy `state/`, `.env`, `alpacabot/`, or `fxbot/`.

From a checkout of this commit (`SRC`):

```bash
STAGE=$(mktemp -d)
mkdir -p "$STAGE/empty-feed" "$STAGE/tmp"
cp -a "$SRC/marv" "$STAGE/marv"
mkdir -p "$STAGE/deploy" "$STAGE/review/card-fixes" "$STAGE/tests"
cp -a "$SRC/deploy/marv-health.sh" "$STAGE/deploy/marv-health.sh"
cp -a "$SRC/review/card-fixes/." "$STAGE/review/card-fixes/"
cp -a "$SRC/tests/test_lane_pause.py" "$SRC/tests/test_decision_audit.py" "$SRC/tests/test_parlay.py" "$STAGE/tests/"
export PYTHONPATH="$STAGE"
export MARV_FEED_ROOT="$STAGE/empty-feed"
export MARV_HEALTH_DIR="$STAGE/tmp/health"
export TMPDIR="$STAGE/tmp"
export PYTHONDONTWRITEBYTECODE=1
cd "$STAGE" && python3 -m unittest tests.test_lane_pause tests.test_decision_audit tests.test_parlay
```

`test_lane_pause` and `test_decision_audit` import `marv.betcard`, `marv.dailypicks`, `marv.parlay`, `marv.lane_pause`, `marv.decision_audit`, `marv.models`, and `marv.state`. Those pull the rest of the `marv` package (including `marv.data`, `marv.engine`, and their imports). Copy the whole `marv/` directory from this commit so none of those imports resolve to `/opt/marv-bot`. The tests also load `review/card-fixes/overlays/feed/marv_lane_pause.py` and `marv_ncaab_card.py` by path, and the synthetic fixture under `review/card-fixes/fixtures/`.

The health cases shell out to `bash` and to `awk` on `PATH`. Point `PATH` at the test's own stand-ins first; the tests do that. They set `MARV_HEALTH_DIR` and `MARV_HEALTH_UPTIME_FILE` themselves. They do not call `runuser` on the real host, do not open `/opt/marv-bot/state`, and do not send.

`/proc/uptime` is decimal seconds (`93599.50` is accepted and converted with `printf "%d"`). `systemctl show -p ExecMainExitTimestampMonotonic --value` is an integer microsecond count, or empty / `0` / `n/a` when the unit has not exited. The export did not include a captured `show` line. `printf "%.6g"` of a ~23-day uptime in microseconds is `1.99703e+12`. Current mawk and gawk `print int(...)` did not emit that token, and the old script with `set -u` exited non-zero on it rather than 0. Those two facts stay in the review; they are not papered over.

Do not run `python -m unittest discover` against the production tree. Other tests write flags a live process would read.

## Rollback

There is nothing to roll back on the host if this plan is followed, because nothing is copied there. If a later approval does change files, roll back by restoring the backed-up bytes of only those paths, or by checking out the merged commit with a method that still preserves dirty tracked files. Do not `git reset --hard`, do not `checkout -f`, and do not delete ledgers.
