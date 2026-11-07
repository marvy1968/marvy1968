"""Muse Prop1 replay entrypoint. Log-only. No orders. No network on this path.

  LIVE_PROPS_SEND=off python3 runner.py --snapshot fixtures/prop_snapshot_v1.sample.json \\
      --state-dir /tmp/muse-prop1-replay

Unset LIVE_PROPS_SEND is refused. The run replaces STATE/live_props/shadow.jsonl.
Keep history with orchestrate.py, which copies each input_hash aside.

There is no live poll here. Muse and Claude each calling a paid odds API would
spend the budget twice, and the two captures would not be the same observation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from prop_snapshot import MODEL_VERSION, ReplayError, SCHEMA_OUT, load_priors, replay_file
from sendgate import sends_allowed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="muse-prop1")
    ap.add_argument("--once", action="store_true", help="replay one snapshot and exit")
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="replay the vendored sample snapshot; still requires --state-dir")
    ap.add_argument("--mode", default="log_only")
    ap.add_argument("--sport", default=None, help="accepted and ignored; the snapshot names the sport")
    args = ap.parse_args(argv)
    if args.mode != "log_only":
        print("mode must stay log_only; this entrypoint does not send")
        return 2
    if sends_allowed(args.mode, {"telegram_enabled": True}):
        print("refusing: sends are enabled")
        return 2
    snapshot = Path(args.snapshot) if args.snapshot else None
    if args.dry_run and snapshot is None:
        snapshot = Path(__file__).parent / "fixtures" / "prop_snapshot_v1.sample.json"
    if snapshot is None:
        print("pass --snapshot FILE or --dry-run. This entrypoint does not poll.")
        return 2
    try:
        n_obs, n_priced, n_abs, dest = replay_file(snapshot, Path(args.state_dir), priors_yaml=load_priors())
    except ReplayError as exc:
        print(f"replay refused: {exc}")
        return 2
    print(f"replay schema={SCHEMA_OUT} model={MODEL_VERSION} observations={n_obs} "
          f"priced={n_priced} abstained={n_abs} shadow={dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
