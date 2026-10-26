from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


from segment4.common import (
    age_seconds,
    atomic_write_json,
    load_segment4_config,
    process_exists,
    read_json,
    resolve_path,
    utc_now_iso,
)


def paths() -> dict[str, Path]:
    config = load_segment4_config()

    files = dict(
        config.get(
            "files",
            {},
        )
    )

    return {
        name: resolve_path(
            str(
                files.get(
                    name,
                    default,
                )
            )
        )
        for (
            name,
            default,
        ) in {
            "lock": (
                "state/segment4/"
                "segment4_daemon.lock"
            ),
            "pid": (
                "state/segment4/"
                "segment4_daemon.pid"
            ),
            "heartbeat": (
                "state/segment4/"
                "heartbeat.json"
            ),
            "status": (
                "state/segment4/"
                "status.json"
            ),
            "shutdown_request": (
                "state/segment4/"
                "SHUTDOWN_REQUEST"
            ),
            "pause": (
                "state/segment4/"
                "pause.json"
            ),
        }.items()
    }


def read_pid(
    pid_path: Path,
) -> int:
    if not pid_path.exists():
        return 0

    try:
        return int(
            pid_path.read_text(
                encoding="utf-8",
            ).strip()
        )

    except (
        ValueError,
        OSError,
    ):
        return 0


def status_payload() -> dict[str, Any]:
    config = load_segment4_config()
    runtime_paths = paths()

    pid = read_pid(
        runtime_paths["pid"]
    )

    heartbeat = read_json(
        runtime_paths[
            "heartbeat"
        ],
        default={},
    )

    status = read_json(
        runtime_paths[
            "status"
        ],
        default={},
    )

    lock_payload = read_json(
        runtime_paths[
            "lock"
        ],
        default={},
    )

    if not isinstance(
        heartbeat,
        dict,
    ):
        heartbeat = {}

    if not isinstance(
        status,
        dict,
    ):
        status = {}

    if not isinstance(
        lock_payload,
        dict,
    ):
        lock_payload = {}

    heartbeat_age = age_seconds(
        str(
            heartbeat.get(
                "timestamp",
                "",
            )
        )
    )

    stale_limit = int(
        config.get(
            "heartbeat_stale_seconds",
            180,
        )
    )

    return {
        "timestamp": utc_now_iso(),
        "enabled": bool(
            config.get(
                "enabled",
                True,
            )
        ),
        "mode": config.get(
            "mode",
            "research_daemon",
        ),
        "practice_only": bool(
            config.get(
                "practice_only",
                True,
            )
        ),
        "automatic_execution": bool(
            config.get(
                "automatic_execution",
                False,
            )
        ),
        (
            "practice_order_"
            "submission_enabled"
        ): bool(
            config.get(
                (
                    "practice_order_"
                    "submission_enabled"
                ),
                False,
            )
        ),
        "pid": pid or None,
        "process_running": (
            process_exists(
                pid
            )
        ),
        "lock_exists": (
            runtime_paths[
                "lock"
            ].exists()
        ),
        "lock": lock_payload,
        "heartbeat_exists": (
            runtime_paths[
                "heartbeat"
            ].exists()
        ),
        "heartbeat_age_seconds": (
            heartbeat_age
        ),
        "heartbeat_stale": (
            heartbeat_age is None
            or heartbeat_age
            > stale_limit
        ),
        "heartbeat": heartbeat,
        "status": status,
        "paused": (
            runtime_paths[
                "pause"
            ].exists()
        ),
        "pause": read_json(
            runtime_paths[
                "pause"
            ],
            default={},
        ),
        "shutdown_requested": (
            runtime_paths[
                "shutdown_request"
            ].exists()
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI Segment 4A-1 "
            "administrative control"
        )
    )

    parser.add_argument(
        "command",
        choices=[
            "status",
            "stop",
            "force-stop",
            "pause",
            "resume",
            "clear-lock",
        ],
    )

    parser.add_argument(
        "--reason",
        default="manual_control",
    )

    arguments = parser.parse_args()
    runtime_paths = paths()

    if arguments.command == "status":
        print(
            json.dumps(
                status_payload(),
                indent=2,
            )
        )

        return

    if arguments.command == "stop":
        runtime_paths[
            "shutdown_request"
        ].parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        runtime_paths[
            "shutdown_request"
        ].write_text(
            (
                "Segment 4A-1 graceful "
                "shutdown requested.\n"
            ),
            encoding="utf-8",
        )

        print(
            "Segment 4A-1 graceful "
            "shutdown requested."
        )

        return

    if arguments.command == "force-stop":
        pid = read_pid(
            runtime_paths[
                "pid"
            ]
        )

        if not pid:
            print(
                "No Segment 4A-1 PID was found."
            )

            return

        if not process_exists(
            pid
        ):
            print(
                "Segment 4A-1 process is not running."
            )

            return

        os.kill(
            pid,
            signal.SIGTERM,
        )

        print(
            "SIGTERM sent to Segment 4A-1 "
            f"PID {pid}."
        )

        return

    if arguments.command == "pause":
        atomic_write_json(
            runtime_paths[
                "pause"
            ],
            {
                "timestamp": utc_now_iso(),
                "reason": arguments.reason,
                "source": (
                    "segment4_control"
                ),
            },
        )

        print(
            "Segment 4A-1 paused."
        )

        return

    if arguments.command == "resume":
        runtime_paths[
            "pause"
        ].unlink(
            missing_ok=True
        )

        print(
            "Segment 4A-1 pause cleared."
        )

        return

    if arguments.command == "clear-lock":
        pid = read_pid(
            runtime_paths[
                "pid"
            ]
        )

        if process_exists(
            pid
        ):
            raise SystemExit(
                "Refusing to clear the lock while "
                f"PID {pid} is running."
            )

        runtime_paths[
            "lock"
        ].unlink(
            missing_ok=True
        )

        runtime_paths[
            "pid"
        ].unlink(
            missing_ok=True
        )

        print(
            "Stale Segment 4A-1 lock cleared."
        )


if __name__ == "__main__":
    main()
