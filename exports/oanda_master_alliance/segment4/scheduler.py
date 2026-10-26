from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SleepDecision:
    seconds: int
    reason: str


class CycleScheduler:
    def __init__(
        self,
        config: dict,
    ) -> None:
        self.cycle_interval_seconds = max(
            1,
            int(
                config.get(
                    "cycle_interval_seconds",
                    60,
                )
            ),
        )

        self.idle_cycle_interval_seconds = max(
            self.cycle_interval_seconds,
            int(
                config.get(
                    "idle_cycle_interval_seconds",
                    120,
                )
            ),
        )

        self.error_retry_seconds = max(
            1,
            int(
                config.get(
                    "error_retry_seconds",
                    30,
                )
            ),
        )

    def next_sleep(
        self,
        *,
        session_allowed: bool,
        successful: bool,
    ) -> SleepDecision:
        if not successful:
            return SleepDecision(
                seconds=self.error_retry_seconds,
                reason="cycle_error_retry",
            )

        if not session_allowed:
            return SleepDecision(
                seconds=self.idle_cycle_interval_seconds,
                reason="inactive_session",
            )

        return SleepDecision(
            seconds=self.cycle_interval_seconds,
            reason="normal_cycle_interval",
        )

    @staticmethod
    def sleep(
        seconds: int,
        *,
        shutdown_requested: callable,
        heartbeat_callback: callable | None = None,
        heartbeat_interval_seconds: int = 30,
    ) -> None:
        remaining = max(
            0,
            int(
                seconds
            ),
        )

        heartbeat_interval = max(
            1,
            int(
                heartbeat_interval_seconds
            ),
        )

        until_heartbeat = heartbeat_interval

        while remaining > 0:
            if shutdown_requested():
                return

            sleep_chunk = min(
                1,
                remaining,
            )

            time.sleep(
                sleep_chunk
            )

            remaining -= sleep_chunk
            until_heartbeat -= sleep_chunk

            if (
                heartbeat_callback is not None
                and until_heartbeat <= 0
            ):
                heartbeat_callback(
                    remaining
                )

                until_heartbeat = heartbeat_interval
