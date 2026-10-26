from __future__ import annotations

from pathlib import Path

from segment4.lock_manager import (
    DaemonLock,
)
from segment4.scheduler import (
    CycleScheduler,
)


def test_scheduler_intervals() -> None:
    scheduler = CycleScheduler(
        {
            "cycle_interval_seconds": 60,
            "idle_cycle_interval_seconds": 120,
            "error_retry_seconds": 30,
        }
    )

    assert (
        scheduler.next_sleep(
            session_allowed=True,
            successful=True,
        ).seconds
        == 60
    )

    assert (
        scheduler.next_sleep(
            session_allowed=False,
            successful=True,
        ).seconds
        == 120
    )

    assert (
        scheduler.next_sleep(
            session_allowed=True,
            successful=False,
        ).seconds
        == 30
    )


def test_lock_acquire_release(
    tmp_path: Path,
) -> None:
    lock_path = (
        tmp_path
        / "daemon.lock"
    )

    pid_path = (
        tmp_path
        / "daemon.pid"
    )

    lock = DaemonLock(
        lock_path=lock_path,
        pid_path=pid_path,
        stale_seconds=600,
    )

    result = lock.acquire()

    assert result.acquired
    assert lock_path.exists()
    assert pid_path.exists()

    lock.release()

    assert not lock_path.exists()
    assert not pid_path.exists()
