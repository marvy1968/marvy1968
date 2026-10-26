from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from segment4.common import (
    atomic_write_json,
    read_json,
    utc_now_iso,
)


@dataclass
class RuntimePaths:
    lock: Path
    pid: Path
    heartbeat: Path
    status: Path
    shutdown_request: Path
    pause: Path
    cycle_log: Path
    event_log: Path
    error_log: Path
    report: Path


class RuntimeState:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
    ) -> None:
        self.paths = paths

    def shutdown_requested(
        self,
    ) -> bool:
        return self.paths.shutdown_request.exists()

    def paused(
        self,
    ) -> bool:
        return self.paths.pause.exists()

    def pause_payload(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.paths.pause,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        return payload

    def write_status(
        self,
        *,
        state: str,
        cycle_number: int,
        daemon_running: bool,
        session_allowed: bool,
        last_cycle_successful: bool | None,
        consecutive_errors: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": utc_now_iso(),
            "state": state,
            "daemon_running": daemon_running,
            "cycle_number": cycle_number,
            "session_allowed": session_allowed,
            "last_cycle_successful": (
                last_cycle_successful
            ),
            "consecutive_errors": (
                consecutive_errors
            ),
            "shutdown_requested": (
                self.shutdown_requested()
            ),
            "paused": self.paused(),
        }

        if details:
            payload["details"] = details

        atomic_write_json(
            self.paths.status,
            payload,
        )

        return payload
