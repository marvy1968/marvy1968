from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.common import (
    atomic_write_json,
    load_segment4_config,
    read_json,
)
from segment4.heartbeat import (
    HeartbeatWriter,
)
from segment4.runtime_state import (
    RuntimePaths,
    RuntimeState,
)


def main() -> None:
    config = load_segment4_config()

    with tempfile.TemporaryDirectory() as directory:
        temporary_root = Path(
            directory
        )

        paths = RuntimePaths(
            lock=(
                temporary_root
                / "daemon.lock"
            ),
            pid=(
                temporary_root
                / "daemon.pid"
            ),
            heartbeat=(
                temporary_root
                / "heartbeat.json"
            ),
            status=(
                temporary_root
                / "status.json"
            ),
            shutdown_request=(
                temporary_root
                / "SHUTDOWN_REQUEST"
            ),
            pause=(
                temporary_root
                / "pause.json"
            ),
            cycle_log=(
                temporary_root
                / "cycles.jsonl"
            ),
            event_log=(
                temporary_root
                / "events.jsonl"
            ),
            error_log=(
                temporary_root
                / "errors.jsonl"
            ),
            report=(
                temporary_root
                / "report.md"
            ),
        )

        runtime = RuntimeState(
            paths=paths
        )

        status = runtime.write_status(
            state="verification",
            cycle_number=1,
            daemon_running=False,
            session_allowed=True,
            last_cycle_successful=True,
            consecutive_errors=0,
            details={
                "research_only": True,
            },
        )

        assert paths.status.exists()
        assert status["state"] == "verification"

        heartbeat = HeartbeatWriter(
            heartbeat_path=(
                paths.heartbeat
            ),
            heartbeat_log_path=(
                temporary_root
                / "heartbeat.jsonl"
            ),
        )

        heartbeat.write(
            alive=False,
            state="verification",
            cycle_number=1,
            session_allowed=True,
            last_cycle_successful=True,
            consecutive_errors=0,
            details={
                "automatic_execution": False,
            },
        )

        heartbeat_payload = read_json(
            paths.heartbeat,
            default={},
        )

        assert heartbeat_payload[
            "state"
        ] == "verification"

        atomic_write_json(
            paths.pause,
            {
                "reason": "verification",
            },
        )

        assert runtime.paused()
        assert (
            runtime.pause_payload()[
                "reason"
            ]
            == "verification"
        )

        paths.pause.unlink()

        paths.shutdown_request.write_text(
            "stop\n",
            encoding="utf-8",
        )

        assert runtime.shutdown_requested()

    compile_command = [
        sys.executable,
        "-m",
        "py_compile",
        "run_segment4.py",
        "admin/segment4_control.py",
        "admin/show_segment4.py",
        "build_segment4a1_report.py",
    ]

    completed = subprocess.run(
        compile_command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, (
        completed.stderr
    )

    result = {
        "status": "passed",
        "runtime_state_write": True,
        "heartbeat_write": True,
        "pause_detection": True,
        "shutdown_detection": True,
        "command_compilation": True,
        "practice_only": (
            config[
                "practice_only"
            ]
        ),
        "automatic_execution": (
            config[
                "automatic_execution"
            ]
        ),
        (
            "practice_order_"
            "submission_enabled"
        ): (
            config[
                (
                    "practice_order_"
                    "submission_enabled"
                )
            ]
        ),
    }

    print()
    print(
        "SEGMENT 4A-1 RUNTIME VERIFICATION"
    )
    print("=" * 72)

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4A-1 runtime "
        "verification passed."
    )


if __name__ == "__main__":
    main()
