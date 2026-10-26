from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

from segment4.common import (
    append_jsonl,
    atomic_write_json,
    utc_now_iso,
)


class HeartbeatWriter:
    def __init__(
        self,
        *,
        heartbeat_path: Path,
        heartbeat_log_path: Path,
    ) -> None:
        self.heartbeat_path = heartbeat_path
        self.heartbeat_log_path = heartbeat_log_path
        self.process_id = os.getpid()
        self.hostname = socket.gethostname()

    def write(
        self,
        *,
        alive: bool,
        state: str,
        cycle_number: int,
        session_allowed: bool,
        last_cycle_successful: bool | None,
        consecutive_errors: int,
        sleeping_seconds: int = 0,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": utc_now_iso(),
            "alive": bool(
                alive
            ),
            "state": str(
                state
            ),
            "pid": self.process_id,
            "hostname": self.hostname,
            "cycle_number": int(
                cycle_number
            ),
            "session_allowed": bool(
                session_allowed
            ),
            "last_cycle_successful": (
                last_cycle_successful
            ),
            "consecutive_errors": int(
                consecutive_errors
            ),
            "sleeping_seconds": int(
                sleeping_seconds
            ),
        }

        if details:
            payload["details"] = details

        atomic_write_json(
            self.heartbeat_path,
            payload,
        )

        append_jsonl(
            self.heartbeat_log_path,
            payload,
        )

        return payload
