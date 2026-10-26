from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
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
    load_segment4_config,
)
from segment4.lock_manager import (
    DaemonLock,
)
from segment4.scheduler import (
    CycleScheduler,
)
from segment4.safety import (
    RuntimeSafetyGate,
)


def main() -> None:
    config = load_segment4_config()

    assert config[
        "practice_only"
    ] is True

    assert config[
        "live_environment_allowed"
    ] is False

    assert config[
        "automatic_execution"
    ] is False

    assert config[
        "practice_order_submission_enabled"
    ] is False

    scheduler = CycleScheduler(
        config
    )

    normal = scheduler.next_sleep(
        session_allowed=True,
        successful=True,
    )

    idle = scheduler.next_sleep(
        session_allowed=False,
        successful=True,
    )

    error = scheduler.next_sleep(
        session_allowed=True,
        successful=False,
    )

    assert normal.seconds > 0
    assert idle.seconds >= normal.seconds
    assert error.seconds > 0

    with tempfile.TemporaryDirectory() as directory:
        directory_path = Path(
            directory
        )

        lock_path = (
            directory_path
            / "daemon.lock"
        )

        pid_path = (
            directory_path
            / "daemon.pid"
        )

        first_lock = DaemonLock(
            lock_path=lock_path,
            pid_path=pid_path,
            stale_seconds=600,
        )

        first_result = (
            first_lock.acquire()
        )

        assert first_result.acquired
        assert lock_path.exists()
        assert pid_path.exists()

        second_lock = DaemonLock(
            lock_path=lock_path,
            pid_path=pid_path,
            stale_seconds=600,
        )

        second_result = (
            second_lock.acquire()
        )

        assert not second_result.acquired

        first_lock.release()

        assert not lock_path.exists()
        assert not pid_path.exists()

    safety = RuntimeSafetyGate(
        config
    ).evaluate()

    required_files = [
        "segment4/common.py",
        "segment4/models.py",
        "segment4/lock_manager.py",
        "segment4/heartbeat.py",
        "segment4/session_manager.py",
        "segment4/scheduler.py",
        "segment4/safety.py",
        "segment4/runtime_state.py",
        "segment4/execution_cycle.py",
        "segment4/daemon.py",
        "run_segment4.py",
        "admin/segment4_control.py",
        "build_segment4a1_report.py",
    ]

    missing = [
        file_name
        for file_name in required_files
        if not (
            PROJECT_ROOT
            / file_name
        ).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4A-1 files: "
            + ", ".join(
                missing
            )
        )

    payload = {
        "status": "passed",
        "pid": os.getpid(),
        "required_files": len(
            required_files
        ),
        "practice_only": (
            config[
                "practice_only"
            ]
        ),
        "live_environment_allowed": (
            config[
                "live_environment_allowed"
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
        "normal_sleep_seconds": (
            normal.seconds
        ),
        "idle_sleep_seconds": (
            idle.seconds
        ),
        "error_retry_seconds": (
            error.seconds
        ),
        "safety_allowed": (
            safety.allowed
        ),
        "safety_reasons": (
            safety.reasons
        ),
        "safety_warnings": (
            safety.warnings
        ),
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4A-1"
    )

    print("=" * 72)

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4A-1 foundation "
        "verification passed."
    )


if __name__ == "__main__":
    main()
