from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from segment4.common import (
    age_seconds,
    atomic_write_json,
    process_exists,
    read_json,
    utc_now_iso,
)


@dataclass
class LockState:
    acquired: bool
    reason: str
    owner_pid: int | None = None
    stale_lock_removed: bool = False


class DaemonLock:
    def __init__(
        self,
        *,
        lock_path: Path,
        pid_path: Path,
        stale_seconds: int,
    ) -> None:
        self.lock_path = lock_path
        self.pid_path = pid_path
        self.stale_seconds = int(
            stale_seconds
        )

        self.process_id = os.getpid()
        self.hostname = socket.gethostname()
        self.acquired = False

    def _current_payload(
        self,
    ) -> dict[str, Any] | None:
        payload = read_json(
            self.lock_path,
            default=None,
        )

        if not isinstance(
            payload,
            dict,
        ):
            return None

        return payload

    def _is_existing_lock_active(
        self,
        payload: dict[str, Any],
    ) -> bool:
        owner_pid = int(
            payload.get(
                "pid",
                0,
            )
            or 0
        )

        owner_host = str(
            payload.get(
                "hostname",
                "",
            )
        )

        if (
            owner_host
            and owner_host
            != self.hostname
        ):
            timestamp_age = age_seconds(
                str(
                    payload.get(
                        "timestamp",
                        "",
                    )
                )
            )

            return bool(
                timestamp_age is not None
                and timestamp_age
                <= self.stale_seconds
            )

        return process_exists(
            owner_pid
        )

    def acquire(
        self,
    ) -> LockState:
        self.lock_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.pid_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        stale_removed = False
        existing = self._current_payload()

        if existing:
            owner_pid = int(
                existing.get(
                    "pid",
                    0,
                )
                or 0
            )

            if self._is_existing_lock_active(
                existing
            ):
                return LockState(
                    acquired=False,
                    reason=(
                        "another_segment4_daemon_is_running"
                    ),
                    owner_pid=owner_pid,
                    stale_lock_removed=False,
                )

            self.lock_path.unlink(
                missing_ok=True
            )

            self.pid_path.unlink(
                missing_ok=True
            )

            stale_removed = True

        payload = {
            "timestamp": utc_now_iso(),
            "pid": self.process_id,
            "hostname": self.hostname,
            "component": "segment4a1_daemon",
        }

        atomic_write_json(
            self.lock_path,
            payload,
        )

        self.pid_path.write_text(
            f"{self.process_id}\n",
            encoding="utf-8",
        )

        self.acquired = True

        return LockState(
            acquired=True,
            reason="lock_acquired",
            owner_pid=self.process_id,
            stale_lock_removed=stale_removed,
        )

    def refresh(
        self,
    ) -> None:
        if not self.acquired:
            return

        payload = {
            "timestamp": utc_now_iso(),
            "pid": self.process_id,
            "hostname": self.hostname,
            "component": "segment4a1_daemon",
        }

        atomic_write_json(
            self.lock_path,
            payload,
        )

    def release(
        self,
    ) -> None:
        if not self.acquired:
            return

        payload = self._current_payload()

        if payload:
            owner_pid = int(
                payload.get(
                    "pid",
                    0,
                )
                or 0
            )

            if owner_pid not in {
                0,
                self.process_id,
            }:
                return

        self.lock_path.unlink(
            missing_ok=True
        )

        self.pid_path.unlink(
            missing_ok=True
        )

        self.acquired = False

    def __enter__(
        self,
    ) -> "DaemonLock":
        result = self.acquire()

        if not result.acquired:
            raise RuntimeError(
                result.reason
            )

        return self

    def __exit__(
        self,
        exception_type: object,
        exception_value: object,
        traceback: object,
    ) -> None:
        self.release()
