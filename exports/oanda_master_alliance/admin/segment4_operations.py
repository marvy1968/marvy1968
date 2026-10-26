from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.common import (
    process_exists,
    read_json,
    utc_now_iso,
)
from segment4.operations_common import (
    operations_paths,
    read_pid_file,
)


def status_payload() -> dict[str, Any]:
    paths = operations_paths()

    watchdog_pid = read_pid_file(
        paths[
            "watchdog_pid"
        ]
    )

    return {
        "timestamp": utc_now_iso(),
        "watchdog_pid": (
            watchdog_pid
            or None
        ),
        "watchdog_running": (
            process_exists(
                watchdog_pid
            )
        ),
        "watchdog_status": read_json(
            paths[
                "watchdog_status"
            ],
            default={},
        ),
        "health_status": read_json(
            paths[
                "health_status"
            ],
            default={},
        ),
        "recovery_status": read_json(
            paths[
                "recovery_status"
            ],
            default={},
        ),
        "unresolved_recovery": (
            paths[
                "unresolved_recovery"
            ].exists()
        ),
        "duplicate_registry": read_json(
            paths[
                "duplicate_registry"
            ],
            default={},
        ),
        "restart_history": read_json(
            paths[
                "restart_history"
            ],
            default={},
        ),
    }


def start_watchdog() -> None:
    paths = operations_paths()

    existing_pid = read_pid_file(
        paths[
            "watchdog_pid"
        ]
    )

    if process_exists(
        existing_pid
    ):
        raise SystemExit(
            "Watchdog is already running "
            f"with PID {existing_pid}."
        )

    console_log = (
        ROOT
        / "logs/"
        "segment4_watchdog_console.log"
    )

    with console_log.open(
        "a",
        encoding="utf-8",
    ) as handle:
        process = subprocess.Popen(
            [
                sys.executable,
                "run_segment4_watchdog.py",
            ],
            cwd=str(
                ROOT
            ),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    print(
        "Segment 4A-2 watchdog started "
        f"with PID {process.pid}."
    )


def stop_watchdog(
    *,
    force: bool,
) -> None:
    paths = operations_paths()

    pid = read_pid_file(
        paths[
            "watchdog_pid"
        ]
    )

    if force:
        if not process_exists(
            pid
        ):
            print(
                "Watchdog is not running."
            )

            return

        os.kill(
            pid,
            signal.SIGTERM,
        )

        print(
            f"SIGTERM sent to watchdog PID {pid}."
        )

        return

    paths[
        "watchdog_shutdown"
    ].parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    paths[
        "watchdog_shutdown"
    ].write_text(
        (
            "Segment 4A-2 watchdog "
            "shutdown requested.\n"
        ),
        encoding="utf-8",
    )

    print(
        "Watchdog graceful shutdown requested."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Segment 4A-2 operations control"
        )
    )

    parser.add_argument(
        "command",
        choices=[
            "status",
            "health",
            "recovery",
            "watchdog-once",
            "watchdog-start",
            "watchdog-stop",
            "watchdog-force-stop",
            "clear-unresolved",
        ],
    )

    arguments = parser.parse_args()

    if arguments.command == "status":
        print(
            json.dumps(
                status_payload(),
                indent=2,
            )
        )

    elif arguments.command == "health":
        completed = subprocess.run(
            [
                sys.executable,
                "run_segment4_health.py",
            ],
            cwd=str(
                ROOT
            ),
            check=False,
        )

        raise SystemExit(
            completed.returncode
        )

    elif arguments.command == "recovery":
        completed = subprocess.run(
            [
                sys.executable,
                "run_segment4_recovery.py",
            ],
            cwd=str(
                ROOT
            ),
            check=False,
        )

        raise SystemExit(
            completed.returncode
        )

    elif arguments.command == "watchdog-once":
        completed = subprocess.run(
            [
                sys.executable,
                "run_segment4_watchdog.py",
                "--once",
            ],
            cwd=str(
                ROOT
            ),
            check=False,
        )

        raise SystemExit(
            completed.returncode
        )

    elif arguments.command == "watchdog-start":
        start_watchdog()

    elif arguments.command == "watchdog-stop":
        stop_watchdog(
            force=False
        )

    elif (
        arguments.command
        == "watchdog-force-stop"
    ):
        stop_watchdog(
            force=True
        )

    elif (
        arguments.command
        == "clear-unresolved"
    ):
        path = operations_paths()[
            "unresolved_recovery"
        ]

        path.unlink(
            missing_ok=True
        )

        print(
            "Unresolved-recovery block cleared."
        )


if __name__ == "__main__":
    main()
