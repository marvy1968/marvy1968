"""Archive Muse and Claude replays without polling and without losing history.

Claude's replay replaces state/live_props/shadow.jsonl on every invocation.
This tool copies each input_hash into its own directory before that can happen,
replays Muse into a separate state dir, and writes the comparison there.

It does not call ESPN, The Odds API, Telegram, or a broker. A collector that
builds the raw snapshot is still required and is not this program.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from compare_shadow import atomic_write, compare, load_shadow, write_report
from prop_snapshot import ReplayError, assert_replay_state, hash_payload, input_hash, load_snapshot, replay_file

INDEX = "recommendation_index.json"


def _ids_from_index(index: dict, this_hash: str | None = None) -> set[str]:
    """Ids recommended by other snapshots. The current hash stays replayable."""
    out = set()
    for rid, meta in (index.get("recommendations") or {}).items():
        if this_hash is not None and meta.get("input_hash") == this_hash:
            continue
        out.add(rid)
    return out


def _load_index(path: Path) -> dict:
    if not path.exists():
        return {"schema": "muse-prop1.recommendation-index.v1", "recommendations": {}}
    return json.loads(path.read_text())


def _update_index(index: dict, rows: list[dict], this_hash: str) -> dict:
    recs = {rid: meta for rid, meta in index.get("recommendations", {}).items()
            if meta.get("input_hash") != this_hash}
    for row in rows:
        if not row.get("recommended"):
            continue
        from prop_snapshot import recommendation_id
        rid = recommendation_id(row)
        recs[rid] = {"input_hash": row.get("input_hash"), "cohort_key": row.get("cohort_key")}
    return {"schema": "muse-prop1.recommendation-index.v1", "recommendations": recs}


def orchestrate(snapshot: Path, archive: Path, claude_shadow: Path | None = None,
                muse_state: Path | None = None, cap: int = 3) -> dict:
    archive = assert_replay_state(archive)
    muse_state = assert_replay_state(muse_state or (archive / "muse-state"))
    doc = load_snapshot(snapshot)
    index_path = archive / INDEX
    index = _load_index(index_path)
    this_file = []
    for i, obs in enumerate(doc["observations"]):
        if not isinstance(obs, dict):
            raise ReplayError(f"observations[{i}] must be an object")
        this_file.append(input_hash(hash_payload(obs, f"observations[{i}]")))
    prior = set()
    for rid, meta in (index.get("recommendations") or {}).items():
        if meta.get("input_hash") not in this_file:
            prior.add(rid)
    n_obs, n_priced, n_abs, muse_path = replay_file(snapshot, muse_state, already=prior, cap=cap)
    muse_rows = load_shadow(muse_path)
    by_hash: dict[str, list] = {}
    for row in muse_rows:
        by_hash.setdefault(row["input_hash"], []).append(row)
    claude_rows = load_shadow(claude_shadow) if claude_shadow else []
    claude_by: dict[str, list] = {}
    for row in claude_rows:
        claude_by.setdefault(row.get("input_hash"), []).append(row)
    written = []
    for digest, rows in sorted(by_hash.items()):
        dest = archive / "by-hash" / digest.removeprefix("sha256:") / "muse" / "shadow.jsonl"
        atomic_write(dest, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        claude_dest = None
        if digest in claude_by:
            claude_dest = archive / "by-hash" / digest.removeprefix("sha256:") / "claude" / "shadow.jsonl"
            atomic_write(claude_dest, "".join(json.dumps(r, sort_keys=True) + "\n" for r in claude_by[digest]))
            report = compare(rows, claude_by[digest])
            write_report(archive / "by-hash" / digest.removeprefix("sha256:") / "report.json", report)
        written.append({"input_hash": digest, "muse_rows": len(rows),
                        "claude_rows": len(claude_by.get(digest, [])),
                        "muse_shadow": str(dest), "claude_shadow": str(claude_dest) if claude_dest else None})
    unmatched = sorted(set(claude_by) - set(by_hash))
    for digest in unmatched:
        dest = archive / "unmatched-claude" / digest.removeprefix("sha256:") / "shadow.jsonl"
        atomic_write(dest, "".join(json.dumps(r, sort_keys=True) + "\n" for r in claude_by[digest]))
    new_index = index
    for digest, rows in by_hash.items():
        new_index = _update_index(new_index, rows, digest)
    atomic_write(index_path, json.dumps(new_index, indent=2, sort_keys=True) + "\n")
    manifest = {
        "schema": "muse-prop1.replay-archive.v1",
        "snapshot": str(snapshot),
        "observations": n_obs,
        "muse_priced": n_priced,
        "muse_abstained": n_abs,
        "muse_state_shadow": str(muse_path),
        "claude_shadow_source": str(claude_shadow) if claude_shadow else None,
        "hashes": written,
        "claude_hashes_not_in_snapshot": unmatched,
        "history_note": (
            "Muse and Claude both replace live_props/shadow.jsonl in their state dir. "
            "The by-hash directories are the retained copies. Re-running the same snapshot "
            "replaces only that hash. Other hashes stay."
        ),
        "collector": "not_run",
    }
    atomic_write(archive / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if claude_rows:
        overall = compare(muse_rows, claude_rows)
        write_report(archive / "report.json", overall)
    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="orchestrate")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--claude-shadow", default=None,
                    help="shadow.jsonl already written by Claude replay; copied, not modified")
    ap.add_argument("--muse-state", default=None, help="isolated dir whose shadow.jsonl is replaced")
    args = ap.parse_args(argv)
    try:
        manifest = orchestrate(Path(args.snapshot), Path(args.archive),
                               Path(args.claude_shadow) if args.claude_shadow else None,
                               Path(args.muse_state) if args.muse_state else None)
    except (ReplayError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"orchestrate refused: {exc}")
        return 2
    print(f"archive observations={manifest['observations']} hashes={len(manifest['hashes'])} "
          f"claude_unmatched={len(manifest['claude_hashes_not_in_snapshot'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
