from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from segment4b4.common import (
    atomic_write_json,
    read_json,
    resolve_path,
    utc_now_iso,
)


class CampaignState:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

        self.status_path = resolve_path(
            config,
            "campaign_status",
        )

        self.control_path = resolve_path(
            config,
            "campaign_control",
        )

        self.pause_path = resolve_path(
            config,
            "pause_file",
        )

        self.shutdown_path = resolve_path(
            config,
            "shutdown_file",
        )

        self.lock_path = resolve_path(
            config,
            "campaign_lock",
        )

        self.pid_path = resolve_path(
            config,
            "campaign_pid",
        )

    def initial_status(self) -> dict[str, Any]:
        target = int(
            self.config.get(
                "campaign",
                {},
            ).get(
                "target_closed_trades",
                250,
            )
        )

        return {
            "timestamp": utc_now_iso(),
            "state": "installed_not_started",
            "running": False,
            "pid": None,
            "cycle_number": 0,
            "consecutive_errors": 0,
            "open_shadow_trades": 0,
            "closed_shadow_trades": 0,
            "target_closed_trades": target,
            "campaign_complete": False,
            "paused": False,
            "shutdown_requested": False,
            "automatic_execution": False,
            "order_submission": False,
        }

    def load(self) -> dict[str, Any]:
        return read_json(
            self.status_path,
            default=self.initial_status(),
        )

    def save(
        self,
        payload: dict[str, Any],
    ) -> None:
        safe_payload = dict(payload)

        safe_payload.update(
            {
                "timestamp": utc_now_iso(),
                "automatic_execution": False,
                "order_submission": False,
            }
        )

        atomic_write_json(
            self.status_path,
            safe_payload,
        )

    def paused(self) -> bool:
        return self.pause_path.exists()

    def shutdown_requested(self) -> bool:
        return self.shutdown_path.exists()

    def request_shutdown(self) -> None:
        self.shutdown_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.shutdown_path.write_text(
            "Segment 4B-4 graceful shutdown requested.\n",
            encoding="utf-8",
        )

    def clear_shutdown(self) -> None:
        self.shutdown_path.unlink(
            missing_ok=True
        )

    def pause(
        self,
        reason: str,
    ) -> None:
        self.pause_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.pause_path.write_text(
            reason.strip() + "\n",
            encoding="utf-8",
        )

    def resume(self) -> None:
        self.pause_path.unlink(
            missing_ok=True
        )

    def acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.lock_path.exists():
            raise RuntimeError(
                "Segment 4B-4 campaign lock already exists."
            )

        self.lock_path.write_text(
            str(os.getpid()) + "\n",
            encoding="utf-8",
        )

        self.pid_path.write_text(
            str(os.getpid()) + "\n",
            encoding="utf-8",
        )

    def release_lock(self) -> None:
        self.lock_path.unlink(
            missing_ok=True
        )

        self.pid_path.unlink(
            missing_ok=True
        )
