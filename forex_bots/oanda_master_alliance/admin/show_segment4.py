from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from segment4.common import (
    load_segment4_config,
    read_json,
    resolve_path,
)


def load_paths() -> dict[str, Path]:
    config = load_segment4_config()

    files = dict(
        config.get(
            "files",
            {},
        )
    )

    defaults = {
        "heartbeat": (
            "state/segment4/"
            "heartbeat.json"
        ),
        "status": (
            "state/segment4/"
            "status.json"
        ),
        "pause": (
            "state/segment4/"
            "pause.json"
        ),
        "lock": (
            "state/segment4/"
            "segment4_daemon.lock"
        ),
        "pid": (
            "state/segment4/"
            "segment4_daemon.pid"
        ),
    }

    return {
        name: resolve_path(
            str(
                files.get(
                    name,
                    default,
                )
            )
        )
        for name, default in defaults.items()
    }


def show_summary() -> None:
    paths = load_paths()

    status = read_json(
        paths["status"],
        default={},
    )

    heartbeat = read_json(
        paths["heartbeat"],
        default={},
    )

    if not isinstance(status, dict):
        status = {}

    if not isinstance(heartbeat, dict):
        heartbeat = {}

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4A-1"
    )
    print("=" * 80)

    print(
        "State:                  "
        f"{status.get('state', 'not_run')}"
    )

    print(
        "Daemon running:         "
        f"{status.get('daemon_running', False)}"
    )

    print(
        "Cycle number:           "
        f"{status.get('cycle_number', 0)}"
    )

    print(
        "Session allowed:        "
        f"{status.get('session_allowed', False)}"
    )

    print(
        "Last cycle successful:  "
        f"{status.get('last_cycle_successful')}"
    )

    print(
        "Consecutive errors:     "
        f"{status.get('consecutive_errors', 0)}"
    )

    print(
        "Heartbeat state:        "
        f"{heartbeat.get('state', 'missing')}"
    )

    print(
        "Heartbeat timestamp:    "
        f"{heartbeat.get('timestamp', 'missing')}"
    )

    print(
        "Paused:                 "
        f"{paths['pause'].exists()}"
    )

    print(
        "Lock present:           "
        f"{paths['lock'].exists()}"
    )

    print(
        "PID file present:       "
        f"{paths['pid'].exists()}"
    )

    print(
        "Automatic execution:    False"
    )

    print(
        "Order submission:       False"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Display Segment 4A-1 state."
        )
    )

    parser.add_argument(
        "view",
        choices=[
            "summary",
            "status",
            "heartbeat",
            "pause",
        ],
        default="summary",
        nargs="?",
    )

    arguments = parser.parse_args()
    paths = load_paths()

    if arguments.view == "summary":
        show_summary()
        return

    path = paths[
        arguments.view
    ]

    if not path.exists():
        raise SystemExit(
            f"{path} has not been created"
        )

    payload = read_json(
        path,
        default={},
    )

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
