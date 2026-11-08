# Source provenance

Review base: `review/odds-accounting-base-20261108`, the fetched `claude/analysis-ak180w` snapshot `b38135da31a760c17b991662910ed52f873840c2`. That deployed branch is not the PR target and was not modified.

Screened export read 2026-11-08 16:15:49 UTC. Each source was stable for its own read. This is not an atomic multi-repository snapshot.

| Tree | VM path | Commit |
|---|---|---|
| AJ | `/home/moonrider1968/aj-bet-bot` | `4470ca6176c0855b9ecdb6a3b274234d7efb4bf2`, no remote |
| Marv shared | `/opt/marv-bot` | `e7d1f50c048735a2348e1f70ac1fbc538640dcbc` |
| VM feed | `/opt/marv-feed` | `a5d7bdbdc6be9e03383c15b790ef871a5e42e583`, no remote |
| Muse | `/opt/prop-shadow/muse-src` | `c3cb88decf52bc7ff64a772af516523883795543` |
| Claude worktree | `/opt/prop-shadow/claude-src` | `0155dbca20e5143c4d92e7ff2b47726f716e891d` |

Byte-identical review copies of the screened export:

| File | SHA256 |
|---|---|
| `integrations/ajbet/db.py` | `b584f4aaec9edc3b439327ef95d025772c44e66be4aad3571b8f5fc66b99bd57` |
| `integrations/ajbet/sat_priority.py` | `af356f7bd114b430724d0133d2e3e176fb54b64a01c4b6ceb6bb06fa5f171878` |
| `integrations/ajbet/providers/base.py` | `63c05ea0d673f9544ed3795182bdfc5170d1c1c743a730295083d0b07fb18be1` |
| `integrations/ajbet/providers/theoddsapi.py` | `70b2ed57ed394e7d33dd1d0b849137fa0f9b551ca026fefc6a00a76d9d1fc9dc` |
| `integrations/shadow_collector/credits.py` | `e32f73e4a4ff2cca6faaedd677791dbd292def79b8cc92ae9ed629c547e13ac4` |

`integrations/ajbet/oddsapi.py` started from export SHA256 `45e43b187777f656172090841b8214ce917f4b9c10f813180205bfb275be4ddc`. The request, budget check, usage insert, fallback, and return paths are the same. The added calls only note the attempt.

`integrations/shadow_collector/odds_request.py` is the collector's `_odds_request`, `_safe_odds_error`, and `replay_child_env` with the note added. The rest of the collector, including the child replay runner, was not copied and was not changed.

These Marv files matched the export before the note was added: `marv/data/oddsapi.py`, `marv/props/odds.py`, `marv/sharpgap.py`, `marv/parlay.py`. `Budget.charge` is unchanged.

No credentials, environment files, key hashes, production databases, or raw export archive are in this review.
