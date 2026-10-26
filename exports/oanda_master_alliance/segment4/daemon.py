from __future__ import annotations

import os
import signal
import time
import traceback
from pathlib import Path
from typing import Any

from segment4.common import (
    append_jsonl,
    atomic_write_json,
    load_segment4_config,
    resolve_path,
    utc_now_iso,
)
from segment4.execution_cycle import (
    ExecutionCycle,
)
from segment4.heartbeat import (
    HeartbeatWriter,
)
from segment4.lock_manager import (
    DaemonLock,
)
from segment4.runtime_state import (
    RuntimePaths,
    RuntimeState,
)
from segment4.scheduler import (
    CycleScheduler,
)
from segment4.startup_guard import (
    StartupGuard,
)


class Segment4Daemon:
    def __init__(
        self,
        *,
        once: bool = False,
        force_session: bool = False,
    ) -> None:
        self.config = load_segment4_config()

        self.once = bool(
            once
        )

        self.force_session = bool(
            force_session
        )

        files = dict(
            self.config.get(
                "files",
                {},
            )
        )

        self.paths = RuntimePaths(
            lock=resolve_path(
                str(
                    files.get(
                        "lock",
                        (
                            "state/segment4/"
                            "segment4_daemon.lock"
                        ),
                    )
                )
            ),
            pid=resolve_path(
                str(
                    files.get(
                        "pid",
                        (
                            "state/segment4/"
                            "segment4_daemon.pid"
                        ),
                    )
                )
            ),
            heartbeat=resolve_path(
                str(
                    files.get(
                        "heartbeat",
                        (
                            "state/segment4/"
                            "heartbeat.json"
                        ),
                    )
                )
            ),
            status=resolve_path(
                str(
                    files.get(
                        "status",
                        (
                            "state/segment4/"
                            "status.json"
                        ),
                    )
                )
            ),
            shutdown_request=resolve_path(
                str(
                    files.get(
                        "shutdown_request",
                        (
                            "state/segment4/"
                            "SHUTDOWN_REQUEST"
                        ),
                    )
                )
            ),
            pause=resolve_path(
                str(
                    files.get(
                        "pause",
                        (
                            "state/segment4/"
                            "pause.json"
                        ),
                    )
                )
            ),
            cycle_log=resolve_path(
                str(
                    files.get(
                        "cycle_log",
                        (
                            "logs/"
                            "segment4_cycles.jsonl"
                        ),
                    )
                )
            ),
            event_log=resolve_path(
                str(
                    files.get(
                        "event_log",
                        (
                            "logs/"
                            "segment4_events.jsonl"
                        ),
                    )
                )
            ),
            error_log=resolve_path(
                str(
                    files.get(
                        "error_log",
                        (
                            "logs/"
                            "segment4_errors.jsonl"
                        ),
                    )
                )
            ),
            report=resolve_path(
                str(
                    files.get(
                        "report",
                        (
                            "reports/segment4/"
                            "segment4a1_report.md"
                        ),
                    )
                )
            ),
        )

        self.heartbeat_log = (
            self.paths.heartbeat.parent.parent.parent
            / "logs/segment4_heartbeat.jsonl"
        )

        self.runtime_state = RuntimeState(
            paths=self.paths
        )

        self.lock = DaemonLock(
            lock_path=self.paths.lock,
            pid_path=self.paths.pid,
            stale_seconds=int(
                self.config.get(
                    "lock_stale_seconds",
                    600,
                )
            ),
        )

        self.heartbeat = HeartbeatWriter(
            heartbeat_path=(
                self.paths.heartbeat
            ),
            heartbeat_log_path=(
                self.heartbeat_log
            ),
        )

        self.scheduler = CycleScheduler(
            self.config
        )

        self.execution_cycle = ExecutionCycle(
            self.config,
            cycle_log_path=(
                self.paths.cycle_log
            ),
            event_log_path=(
                self.paths.event_log
            ),
            error_log_path=(
                self.paths.error_log
            ),
        )

        self.startup_guard = StartupGuard()

        self.running = False
        self.signal_received = ""
        self.cycle_number = 0
        self.consecutive_errors = 0
        self.last_cycle_successful: (
            bool
            | None
        ) = None
        self.last_session_allowed = False
        self.last_cycle_details: dict[
            str,
            Any,
        ] = {}

    def _install_signal_handlers(
        self,
    ) -> None:
        def handler(
            signal_number: int,
            frame: object,
        ) -> None:
            del frame

            try:
                signal_name = signal.Signals(
                    signal_number
                ).name

            except ValueError:
                signal_name = str(
                    signal_number
                )

            self.signal_received = (
                signal_name
            )

            self.running = False

            append_jsonl(
                self.paths.event_log,
                {
                    "timestamp": (
                        utc_now_iso()
                    ),
                    "event": (
                        "segment4_shutdown_signal"
                    ),
                    "signal": (
                        signal_name
                    ),
                    "pid": os.getpid(),
                },
            )

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
        return (
            not self.running
            or self.runtime_state
            .shutdown_requested()
        )

    def _write_runtime(
        self,
        *,
        state: str,
        sleeping_seconds: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        combined_details: dict[
            str,
            Any,
        ] = {
            "once": self.once,
            "force_session": (
                self.force_session
            ),
            "signal_received": (
                self.signal_received
            ),
        }

        if self.last_cycle_details:
            combined_details[
                "last_cycle"
            ] = self.last_cycle_details

        if details:
            combined_details.update(
                details
            )

        self.runtime_state.write_status(
            state=state,
            cycle_number=(
                self.cycle_number
            ),
            daemon_running=(
                self.running
            ),
            session_allowed=(
                self.last_session_allowed
            ),
            last_cycle_successful=(
                self.last_cycle_successful
            ),
            consecutive_errors=(
                self.consecutive_errors
            ),
            details=combined_details,
        )

        self.heartbeat.write(
            alive=self.running,
            state=state,
            cycle_number=(
                self.cycle_number
            ),
            session_allowed=(
                self.last_session_allowed
            ),
            last_cycle_successful=(
                self.last_cycle_successful
            ),
            consecutive_errors=(
                self.consecutive_errors
            ),
            sleeping_seconds=(
                sleeping_seconds
            ),
            details=combined_details,
        )

    def _pause_due_to_errors(
        self,
    ) -> None:
        maximum_errors = max(
            1,
            int(
                self.config.get(
                    (
                        "maximum_consecutive_"
                        "cycle_errors"
                    ),
                    5,
                )
            ),
        )

        if (
            self.consecutive_errors
            < maximum_errors
        ):
            return

        pause_minutes = max(
            1,
            int(
                self.config.get(
                    (
                        "pause_after_cycle_"
                        "errors_minutes"
                    ),
                    15,
                )
            ),
        )

        payload = {
            "timestamp": utc_now_iso(),
            "reason": (
                "maximum_consecutive_"
                "cycle_errors"
            ),
            "consecutive_errors": (
                self.consecutive_errors
            ),
            "pause_minutes": pause_minutes,
            "source": (
                "segment4a1_daemon"
            ),
        }

        atomic_write_json(
            self.paths.pause,
            payload,
        )

        append_jsonl(
            self.paths.error_log,
            {
                "timestamp": (
                    utc_now_iso()
                ),
                "event": (
                    "segment4_error_pause_created"
                ),
                **payload,
            },
        )

        self.running = False

    def run(
        self,
    ) -> int:
        if not bool(
            self.config.get(
                "enabled",
                True,
            )
        ):
            raise SystemExit(
                "Segment 4A-1 is disabled in "
                "config/segment4.yaml"
            )

        if not bool(
            self.config.get(
                "practice_only",
                True,
            )
        ):
            raise SystemExit(
                "Segment 4A-1 refuses to run "
                "without practice_only=true"
            )

        if bool(
            self.config.get(
                "live_environment_allowed",
                False,
            )
        ):
            raise SystemExit(
                "Segment 4A-1 refuses to run "
                "when live_environment_allowed=true"
            )

        if bool(
            self.config.get(
                "automatic_execution",
                False,
            )
        ):
            raise SystemExit(
                "Segment 4A-1 automatic execution "
                "must remain disabled"
            )

        if bool(
            self.config.get(
                (
                    "practice_order_"
                    "submission_enabled"
                ),
                False,
            )
        ):
            raise SystemExit(
                "Segment 4A-1 practice order "
                "submission must remain disabled"
            )

        startup_decision = (
            self.startup_guard.evaluate()
        )

        if not startup_decision[
            "allowed"
        ]:
            append_jsonl(
                self.paths.error_log,
                {
                    "timestamp": utc_now_iso(),
                    "event": (
                        "segment4_startup_recovery_block"
                    ),
                    "reason": (
                        startup_decision[
                            "reason"
                        ]
                    ),
                    "recovery": (
                        startup_decision[
                            "recovery"
                        ]
                    ),
                },
            )

            print(
                "Segment 4A-1 startup blocked "
                "by Segment 4A-2 recovery."
            )

            print(
                "Reason: "
                + str(
                    startup_decision[
                        "reason"
                    ]
                )
            )

            return 3

        lock_result = self.lock.acquire()

        if not lock_result.acquired:
            print(
                "Segment 4A-1 did not start:"
            )

            print(
                f"  {lock_result.reason}"
            )

            if lock_result.owner_pid:
                print(
                    "  Existing PID: "
                    f"{lock_result.owner_pid}"
                )

            return 2

        self._install_signal_handlers()

        self.paths.shutdown_request.unlink(
            missing_ok=True
        )

        startup_grace = max(
            0,
            int(
                self.config.get(
                    "startup_grace_seconds",
                    2,
                )
            ),
        )

        self.running = True

        append_jsonl(
            self.paths.event_log,
            {
                "timestamp": utc_now_iso(),
                "event": (
                    "segment4_daemon_started"
                ),
                "pid": os.getpid(),
                "once": self.once,
                "force_session": (
                    self.force_session
                ),
                "practice_only": True,
                "automatic_execution": (
                    False
                ),
                "order_submission": False,
                "stale_lock_removed": (
                    lock_result
                    .stale_lock_removed
                ),
            },
        )

        self._write_runtime(
            state="starting",
            sleeping_seconds=(
                startup_grace
            ),
        )

        if startup_grace:
            time.sleep(
                startup_grace
            )

        exit_code = 0

        try:
            while self.running:
                if self.runtime_state.shutdown_requested():
                    self.signal_received = (
                        "shutdown_request_file"
                    )

                    break

                if self.runtime_state.paused():
                    self.last_cycle_successful = (
                        True
                    )

                    self.last_session_allowed = (
                        False
                    )

                    self._write_runtime(
                        state="paused",
                        details={
                            "pause": (
                                self.runtime_state
                                .pause_payload()
                            ),
                        },
                    )

                    if self.once:
                        break

                    sleep_decision = (
                        self.scheduler.next_sleep(
                            session_allowed=False,
                            successful=True,
                        )
                    )

                    self.scheduler.sleep(
                        sleep_decision.seconds,
                        shutdown_requested=(
                            self._shutdown_requested
                        ),
                        heartbeat_callback=(
                            lambda remaining: (
                                self._write_runtime(
                                    state="paused",
                                    sleeping_seconds=(
                                        remaining
                                    ),
                                    details={
                                        "sleep_reason": (
                                            sleep_decision
                                            .reason
                                        ),
                                    },
                                )
                            )
                        ),
                        heartbeat_interval_seconds=int(
                            self.config.get(
                                (
                                    "heartbeat_interval_"
                                    "seconds"
                                ),
                                30,
                            )
                        ),
                    )

                    continue

                self.cycle_number += 1

                self._write_runtime(
                    state="running_cycle",
                )

                try:
                    result = (
                        self.execution_cycle.run(
                            cycle_number=(
                                self.cycle_number
                            ),
                            force_session=(
                                self.force_session
                            ),
                        )
                    )

                    self.last_cycle_successful = (
                        result.successful
                    )

                    self.last_session_allowed = (
                        result.session_allowed
                    )

                    self.last_cycle_details = {
                        "cycle_id": (
                            result.cycle_id
                        ),
                        "started_at": (
                            result.started_at
                        ),
                        "finished_at": (
                            result.finished_at
                        ),
                        "duration_seconds": (
                            result.duration_seconds
                        ),
                        "session_reason": (
                            result.session_reason
                        ),
                        "successful": (
                            result.successful
                        ),
                        "shortlist_count": (
                            result.shortlist_count
                        ),
                        (
                            "risk_approved_"
                            "candidates"
                        ): (
                            result
                            .risk_approved_candidates
                        ),
                        "orders_submitted": (
                            result.orders_submitted
                        ),
                        "error": result.error,
                    }

                    if result.successful:
                        self.consecutive_errors = 0
                    else:
                        self.consecutive_errors += 1

                except Exception as exc:
                    self.last_cycle_successful = (
                        False
                    )

                    self.last_session_allowed = (
                        False
                    )

                    self.consecutive_errors += 1

                    error_text = (
                        f"{type(exc).__name__}:"
                        f"{exc}"
                    )

                    self.last_cycle_details = {
                        "successful": False,
                        "error": error_text,
                    }

                    append_jsonl(
                        self.paths.error_log,
                        {
                            "timestamp": (
                                utc_now_iso()
                            ),
                            "event": (
                                "segment4_unhandled_"
                                "cycle_exception"
                            ),
                            "cycle_number": (
                                self.cycle_number
                            ),
                            "error": error_text,
                            "traceback": (
                                traceback.format_exc()
                            ),
                        },
                    )

                self.lock.refresh()

                self._pause_due_to_errors()

                self._write_runtime(
                    state=(
                        "cycle_complete"
                        if self.last_cycle_successful
                        else "cycle_failed"
                    ),
                )

                if self.once:
                    if not self.last_cycle_successful:
                        exit_code = 1

                    break

                if not self.running:
                    break

                sleep_decision = (
                    self.scheduler.next_sleep(
                        session_allowed=(
                            self.last_session_allowed
                        ),
                        successful=bool(
                            self.last_cycle_successful
                        ),
                    )
                )

                self._write_runtime(
                    state="sleeping",
                    sleeping_seconds=(
                        sleep_decision.seconds
                    ),
                    details={
                        "sleep_reason": (
                            sleep_decision.reason
                        ),
                    },
                )

                self.scheduler.sleep(
                    sleep_decision.seconds,
                    shutdown_requested=(
                        self._shutdown_requested
                    ),
                    heartbeat_callback=(
                        lambda remaining: (
                            self._write_runtime(
                                state="sleeping",
                                sleeping_seconds=(
                                    remaining
                                ),
                                details={
                                    "sleep_reason": (
                                        sleep_decision
                                        .reason
                                    ),
                                },
                            )
                        )
                    ),
                    heartbeat_interval_seconds=int(
                        self.config.get(
                            (
                                "heartbeat_interval_"
                                "seconds"
                            ),
                            30,
                        )
                    ),
                )

        finally:
            self.running = False

            shutdown_grace = max(
                0,
                int(
                    self.config.get(
                        "shutdown_grace_seconds",
                        5,
                    )
                ),
            )

            self._write_runtime(
                state="stopping",
                sleeping_seconds=(
                    shutdown_grace
                ),
            )

            if shutdown_grace:
                time.sleep(
                    min(
                        shutdown_grace,
                        2,
                    )
                )

            append_jsonl(
                self.paths.event_log,
                {
                    "timestamp": (
                        utc_now_iso()
                    ),
                    "event": (
                        "segment4_daemon_stopped"
                    ),
                    "pid": os.getpid(),
                    "cycle_number": (
                        self.cycle_number
                    ),
                    "signal_received": (
                        self.signal_received
                    ),
                    "exit_code": exit_code,
                },
            )

            self._write_runtime(
                state="stopped",
            )

            self.lock.release()

            self.paths.shutdown_request.unlink(
                missing_ok=True
            )

        return exit_code
