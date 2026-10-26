from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from segment4.common import (
    ROOT,
    age_seconds,
    append_jsonl,
    atomic_write_json,
    process_exists,
    read_json,
    utc_now,
    utc_now_iso,
)
from segment4.health_monitor import (
    HealthMonitor,
)
from segment4.operations_common import (
    load_operations_config,
    operations_paths,
    read_pid_file,
    safe_hostname,
)


class WatchdogLock:
    def __init__(
        self,
        *,
        lock_path: Path,
        pid_path: Path,
    ) -> None:
        self.lock_path = lock_path
        self.pid_path = pid_path
        self.pid = os.getpid()
        self.acquired = False

    def acquire(
        self,
    ) -> bool:
        existing_pid = read_pid_file(
            self.pid_path
        )

        if (
            existing_pid
            and process_exists(
                existing_pid
            )
        ):
            return False

        self.lock_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.pid_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        atomic_write_json(
            self.lock_path,
            {
                "timestamp": utc_now_iso(),
                "pid": self.pid,
                "hostname": safe_hostname(),
                "component": (
                    "segment4a2_watchdog"
                ),
            },
        )

        self.pid_path.write_text(
            f"{self.pid}\n",
            encoding="utf-8",
        )

        self.acquired = True
        return True

    def release(
        self,
    ) -> None:
        if not self.acquired:
            return

        current_pid = read_pid_file(
            self.pid_path
        )

        if current_pid in {
            0,
            self.pid,
        }:
            self.lock_path.unlink(
                missing_ok=True
            )

            self.pid_path.unlink(
                missing_ok=True
            )

        self.acquired = False


class RestartHistory:
    def __init__(
        self,
        path: Path,
        config: dict[str, Any],
    ) -> None:
        self.path = path
        self.config = config

    def _load(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        payload.setdefault(
            "attempts",
            []
        )

        return payload

    def _window_start(
        self,
    ) -> datetime:
        minutes = max(
            1,
            int(
                self.config.get(
                    "restart_window_minutes",
                    60,
                )
            ),
        )

        return (
            utc_now()
            - timedelta(
                minutes=minutes
            )
        )

    def recent_attempts(
        self,
    ) -> list[dict[str, Any]]:
        payload = self._load()
        window_start = self._window_start()

        recent: list[
            dict[str, Any]
        ] = []

        for record in payload.get(
            "attempts",
            [],
        ):
            if not isinstance(
                record,
                dict,
            ):
                continue

            try:
                timestamp = datetime.fromisoformat(
                    str(
                        record.get(
                            "timestamp",
                            "",
                        )
                    )
                )

            except ValueError:
                continue

            if timestamp.tzinfo is None:
                timestamp = timestamp.astimezone()

            if timestamp >= window_start:
                recent.append(
                    record
                )

        return recent

    def allowed(
        self,
    ) -> bool:
        maximum = max(
            0,
            int(
                self.config.get(
                    "maximum_restart_attempts",
                    3,
                )
            ),
        )

        return (
            len(
                self.recent_attempts()
            )
            < maximum
        )

    def next_backoff_seconds(
        self,
    ) -> int:
        values = self.config.get(
            "restart_backoff_seconds",
            [
                30,
                60,
                180,
            ],
        )

        if not isinstance(
            values,
            list,
        ) or not values:
            values = [
                30,
            ]

        attempt_index = len(
            self.recent_attempts()
        )

        selected_index = min(
            attempt_index,
            len(
                values
            )
            - 1,
        )

        try:
            return max(
                1,
                int(
                    values[
                        selected_index
                    ]
                ),
            )

        except (
            TypeError,
            ValueError,
        ):
            return 30

    def record(
        self,
        *,
        reason: str,
        pid: int | None,
        successful_launch: bool,
    ) -> None:
        payload = self._load()

        attempts = self.recent_attempts()

        attempts.append(
            {
                "timestamp": utc_now_iso(),
                "reason": reason,
                "pid": pid,
                "successful_launch": (
                    successful_launch
                ),
            }
        )

        payload[
            "attempts"
        ] = attempts

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.path,
            payload,
        )


class Segment4Watchdog:
    def __init__(
        self,
        *,
        once: bool = False,
    ) -> None:
        self.config = (
            load_operations_config()
        )

        self.paths = operations_paths()
        self.once = bool(
            once
        )

        self.running = False
        self.signal_received = ""

        self.main_pid_path = (
            ROOT
            / "state/segment4/"
            "segment4_daemon.pid"
        )

        self.main_status_path = (
            ROOT
            / "state/segment4/"
            "status.json"
        )

        self.main_heartbeat_path = (
            ROOT
            / "state/segment4/"
            "heartbeat.json"
        )

        self.main_lock_path = (
            ROOT
            / "state/segment4/"
            "segment4_daemon.lock"
        )

        self.main_pause_path = (
            ROOT
            / "state/segment4/"
            "pause.json"
        )

        self.segment3_kill_switch = (
            ROOT
            / "state/"
            "SEGMENT3_KILL_SWITCH"
        )

        self.segment3_pause = (
            ROOT
            / "state/execution/"
            "segment3_pause.json"
        )

        self.watchdog_lock = WatchdogLock(
            lock_path=self.paths[
                "watchdog_lock"
            ],
            pid_path=self.paths[
                "watchdog_pid"
            ],
        )

        self.health_monitor = (
            HealthMonitor()
        )

        self.restart_history = RestartHistory(
            self.paths[
                "restart_history"
            ],
            self.config,
        )

        self.heartbeat_failures = 0
        self.process_failures = 0

    def _install_signals(
        self,
    ) -> None:
        def handler(
            signum: int,
            frame: object,
        ) -> None:
            del frame

            try:
                self.signal_received = (
                    signal.Signals(
                        signum
                    ).name
                )

            except ValueError:
                self.signal_received = str(
                    signum
                )

            self.running = False

        signal.signal(
            signal.SIGINT,
            handler,
        )

        signal.signal(
            signal.SIGTERM,
            handler,
        )

    def _shutdown_requested(
        self,
    ) -> bool:
        return self.paths[
            "watchdog_shutdown"
        ].exists()

    def _main_process_status(
        self,
    ) -> tuple[
        int,
        bool,
    ]:
        pid = read_pid_file(
            self.main_pid_path
        )

        return (
            pid,
            process_exists(
                pid
            ),
        )

    def _heartbeat_fresh(
        self,
    ) -> tuple[
        bool,
        float | None,
    ]:
        payload = read_json(
            self.main_heartbeat_path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        age = age_seconds(
            str(
                payload.get(
                    "timestamp",
                    "",
                )
            )
        )

        maximum = float(
            self.config.get(
                (
                    "maximum_heartbeat_"
                    "age_seconds"
                ),
                180,
            )
        )

        return (
            age is not None
            and age <= maximum,
            age,
        )

    def _restart_blockers(
        self,
    ) -> list[str]:
        blockers: list[
            str
        ] = []

        if self.main_pause_path.exists():
            blockers.append(
                "segment4_pause_active"
            )

        if self.segment3_kill_switch.exists():
            blockers.append(
                "segment3_kill_switch_active"
            )

        if self.segment3_pause.exists():
            blockers.append(
                "segment3_risk_pause_active"
            )

        if self.paths[
            "unresolved_recovery"
        ].exists():
            blockers.append(
                "unresolved_recovery"
            )

        if not self.restart_history.allowed():
            blockers.append(
                "restart_limit_reached"
            )

        return blockers

    def _launch_main_daemon(
        self,
        *,
        reason: str,
    ) -> tuple[
        bool,
        int | None,
        str,
    ]:
        blockers = self._restart_blockers()

        if blockers:
            return (
                False,
                None,
                ",".join(
                    blockers
                ),
            )

        backoff = (
            self.restart_history
            .next_backoff_seconds()
        )

        append_jsonl(
            self.paths[
                "recovery_history"
            ],
            {
                "timestamp": utc_now_iso(),
                "event": (
                    "watchdog_restart_backoff"
                ),
                "seconds": backoff,
                "reason": reason,
            },
        )

        time.sleep(
            backoff
        )

        console_log = (
            ROOT
            / "logs/"
            "segment4_console.log"
        )

        console_log.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with console_log.open(
            "a",
            encoding="utf-8",
        ) as handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "run_segment4.py",
                ],
                cwd=str(
                    ROOT
                ),
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        time.sleep(
            2
        )

        successful = process_exists(
            process.pid
        )

        self.restart_history.record(
            reason=reason,
            pid=process.pid,
            successful_launch=successful,
        )

        return (
            successful,
            process.pid,
            (
                "daemon_started"
                if successful
                else "daemon_start_failed"
            ),
        )

    def _write_status(
        self,
        *,
        state: str,
        health: dict[str, Any],
        action: str,
        action_reason: str = "",
        restarted_pid: int | None = None,
    ) -> None:
        main_pid, process_running = (
            self._main_process_status()
        )

        heartbeat_fresh, heartbeat_age = (
            self._heartbeat_fresh()
        )

        payload = {
            "timestamp": utc_now_iso(),
            "state": state,
            "watchdog_pid": os.getpid(),
            "watchdog_running": (
                self.running
            ),
            "main_pid": (
                main_pid
                or None
            ),
            "main_process_running": (
                process_running
            ),
            "heartbeat_fresh": (
                heartbeat_fresh
            ),
            "heartbeat_age_seconds": (
                heartbeat_age
            ),
            "heartbeat_failures": (
                self.heartbeat_failures
            ),
            "process_failures": (
                self.process_failures
            ),
            "health": health,
            "action": action,
            "action_reason": (
                action_reason
            ),
            "restarted_pid": (
                restarted_pid
            ),
            "automatic_restart_enabled": bool(
                self.config.get(
                    (
                        "automatic_restart_"
                        "enabled"
                    ),
                    False,
                )
            ),
            "automatic_execution": False,
            "order_submission": False,
            "signal_received": (
                self.signal_received
            ),
        }

        atomic_write_json(
            self.paths[
                "watchdog_status"
            ],
            payload,
        )

    def inspect_once(
        self,
    ) -> dict[str, Any]:
        report = (
            self.health_monitor.run()
        )

        health_payload = (
            report.to_dict()
        )

        main_pid, process_running = (
            self._main_process_status()
        )

        heartbeat_fresh, heartbeat_age = (
            self._heartbeat_fresh()
        )

        main_status = read_json(
            self.main_status_path,
            default={},
        )

        if not isinstance(
            main_status,
            dict,
        ):
            main_status = {}

        daemon_expected = bool(
            main_status.get(
                "daemon_running",
                False,
            )
        ) or self.main_lock_path.exists()

        if daemon_expected and not process_running:
            self.process_failures += 1
        else:
            self.process_failures = 0

        if daemon_expected and not heartbeat_fresh:
            self.heartbeat_failures += 1
        else:
            self.heartbeat_failures = 0

        process_threshold = max(
            1,
            int(
                self.config.get(
                    "process_failure_threshold",
                    2,
                )
            ),
        )

        heartbeat_threshold = max(
            1,
            int(
                self.config.get(
                    (
                        "heartbeat_failure_"
                        "threshold"
                    ),
                    3,
                )
            ),
        )

        restart_reason = ""

        if (
            self.process_failures
            >= process_threshold
        ):
            restart_reason = (
                "main_process_missing"
            )

        elif (
            self.heartbeat_failures
            >= heartbeat_threshold
        ):
            restart_reason = (
                "heartbeat_stale"
            )

        action = "none"
        action_reason = ""
        restarted_pid: int | None = None

        restart_enabled = bool(
            self.config.get(
                (
                    "automatic_restart_"
                    "enabled"
                ),
                False,
            )
        )

        if restart_reason:
            if restart_enabled:
                (
                    launched,
                    restarted_pid,
                    action_reason,
                ) = self._launch_main_daemon(
                    reason=restart_reason
                )

                action = (
                    "restart_launched"
                    if launched
                    else "restart_blocked"
                )

                if launched:
                    self.process_failures = 0
                    self.heartbeat_failures = 0

            else:
                action = (
                    "restart_recommended"
                )

                action_reason = (
                    "automatic_restart_disabled:"
                    + restart_reason
                )

        state = (
            "healthy"
            if report.healthy
            and not restart_reason
            else "attention_required"
        )

        self._write_status(
            state=state,
            health=health_payload,
            action=action,
            action_reason=action_reason,
            restarted_pid=restarted_pid,
        )

        result = {
            "timestamp": utc_now_iso(),
            "state": state,
            "main_pid": (
                main_pid
                or None
            ),
            "main_process_running": (
                process_running
            ),
            "heartbeat_fresh": (
                heartbeat_fresh
            ),
            "heartbeat_age_seconds": (
                heartbeat_age
            ),
            "restart_reason": (
                restart_reason
            ),
            "action": action,
            "action_reason": (
                action_reason
            ),
            "restarted_pid": (
                restarted_pid
            ),
            "health": health_payload,
        }

        append_jsonl(
            self.paths[
                "health_history"
            ],
            {
                "event": (
                    "watchdog_inspection"
                ),
                **result,
            },
        )

        return result

    def run(
        self,
    ) -> int:
        if not bool(
            self.config.get(
                "watchdog_enabled",
                True,
            )
        ):
            raise SystemExit(
                "Segment 4A-2 watchdog is disabled."
            )

        if not self.watchdog_lock.acquire():
            print(
                "Segment 4A-2 watchdog "
                "is already running."
            )

            return 2

        self._install_signals()

        self.paths[
            "watchdog_shutdown"
        ].unlink(
            missing_ok=True
        )

        self.running = True

        startup_grace = max(
            0,
            int(
                self.config.get(
                    (
                        "watchdog_startup_"
                        "grace_seconds"
                    ),
                    90,
                )
            ),
        )

        append_jsonl(
            self.paths[
                "health_history"
            ],
            {
                "timestamp": utc_now_iso(),
                "event": (
                    "watchdog_started"
                ),
                "pid": os.getpid(),
                "once": self.once,
                "startup_grace_seconds": (
                    startup_grace
                ),
                "automatic_restart_enabled": bool(
                    self.config.get(
                        (
                            "automatic_restart_"
                            "enabled"
                        ),
                        False,
                    )
                ),
            },
        )

        try:
            if (
                startup_grace
                and not self.once
            ):
                time.sleep(
                    startup_grace
                )

            while self.running:
                if self._shutdown_requested():
                    self.signal_received = (
                        "shutdown_request_file"
                    )

                    break

                result = self.inspect_once()

                print(
                    result
                )

                if self.once:
                    break

                interval = max(
                    5,
                    int(
                        self.config.get(
                            (
                                "watchdog_interval_"
                                "seconds"
                            ),
                            30,
                        )
                    ),
                )

                for _ in range(
                    interval
                ):
                    if (
                        not self.running
                        or self._shutdown_requested()
                    ):
                        break

                    time.sleep(
                        1
                    )

        finally:
            self.running = False

            self._write_status(
                state="stopped",
                health={},
                action="none",
                action_reason=(
                    self.signal_received
                ),
            )

            append_jsonl(
                self.paths[
                    "health_history"
                ],
                {
                    "timestamp": utc_now_iso(),
                    "event": (
                        "watchdog_stopped"
                    ),
                    "pid": os.getpid(),
                    "signal_received": (
                        self.signal_received
                    ),
                },
            )

            self.watchdog_lock.release()

            self.paths[
                "watchdog_shutdown"
            ].unlink(
                missing_ok=True
            )

        return 0
