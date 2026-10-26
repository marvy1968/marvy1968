from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from segment4.common import (
    ROOT,
    append_jsonl,
    read_json,
    utc_now_iso,
)
from segment4.models import (
    CycleResult,
    StageResult,
)
from segment4.safety import (
    RuntimeSafetyGate,
)
from segment4.session_manager import (
    SessionDecision,
    SessionManager,
)


def tail_text(
    value: str,
    *,
    maximum_characters: int = 4000,
) -> str:
    text = str(
        value
        or ""
    )

    if len(
        text
    ) <= maximum_characters:
        return text

    return text[
        -maximum_characters:
    ]


class ExecutionCycle:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        cycle_log_path: Path,
        event_log_path: Path,
        error_log_path: Path,
    ) -> None:
        self.config = config
        self.cycle_log_path = cycle_log_path
        self.event_log_path = event_log_path
        self.error_log_path = error_log_path

        self.session_manager = SessionManager(
            config
        )

        self.safety_gate = RuntimeSafetyGate(
            config
        )

        self.maximum_runtime_seconds = max(
            30,
            int(
                config.get(
                    "maximum_cycle_runtime_seconds",
                    180,
                )
            ),
        )

    def _run_command(
        self,
        *,
        name: str,
        command: list[str],
        timeout_seconds: int | None = None,
    ) -> StageResult:
        started_at = utc_now_iso()
        started_monotonic = time.monotonic()

        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.maximum_runtime_seconds
        )

        append_jsonl(
            self.event_log_path,
            {
                "timestamp": started_at,
                "event": "segment4_stage_started",
                "stage": name,
                "command": command,
            },
        )

        try:
            completed = subprocess.run(
                command,
                cwd=str(
                    ROOT
                ),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )

            finished_at = utc_now_iso()
            duration = (
                time.monotonic()
                - started_monotonic
            )

            passed = (
                completed.returncode
                == 0
            )

            result = StageResult(
                name=name,
                attempted=True,
                passed=passed,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(
                    duration,
                    4,
                ),
                return_code=(
                    completed.returncode
                ),
                command=command,
                stdout_tail=tail_text(
                    completed.stdout
                ),
                stderr_tail=tail_text(
                    completed.stderr
                ),
                error=(
                    ""
                    if passed
                    else (
                        "command_returned_nonzero"
                    )
                ),
            )

            append_jsonl(
                self.event_log_path,
                {
                    "timestamp": finished_at,
                    "event": (
                        "segment4_stage_passed"
                        if passed
                        else "segment4_stage_failed"
                    ),
                    "stage": name,
                    "duration_seconds": (
                        result.duration_seconds
                    ),
                    "return_code": (
                        completed.returncode
                    ),
                },
            )

            if not passed:
                append_jsonl(
                    self.error_log_path,
                    {
                        "timestamp": finished_at,
                        "event": (
                            "segment4_stage_error"
                        ),
                        "stage": name,
                        "return_code": (
                            completed.returncode
                        ),
                        "stdout_tail": (
                            result.stdout_tail
                        ),
                        "stderr_tail": (
                            result.stderr_tail
                        ),
                    },
                )

            return result

        except subprocess.TimeoutExpired as exc:
            finished_at = utc_now_iso()
            duration = (
                time.monotonic()
                - started_monotonic
            )

            result = StageResult(
                name=name,
                attempted=True,
                passed=False,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(
                    duration,
                    4,
                ),
                return_code=None,
                command=command,
                stdout_tail=tail_text(
                    exc.stdout
                    if isinstance(
                        exc.stdout,
                        str,
                    )
                    else ""
                ),
                stderr_tail=tail_text(
                    exc.stderr
                    if isinstance(
                        exc.stderr,
                        str,
                    )
                    else ""
                ),
                error=(
                    f"stage_timeout_after_{timeout}s"
                ),
            )

            append_jsonl(
                self.error_log_path,
                {
                    "timestamp": finished_at,
                    "event": (
                        "segment4_stage_timeout"
                    ),
                    "stage": name,
                    "timeout_seconds": timeout,
                },
            )

            return result

        except Exception as exc:
            finished_at = utc_now_iso()
            duration = (
                time.monotonic()
                - started_monotonic
            )

            result = StageResult(
                name=name,
                attempted=True,
                passed=False,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(
                    duration,
                    4,
                ),
                return_code=None,
                command=command,
                error=(
                    f"{type(exc).__name__}:"
                    f"{exc}"
                ),
            )

            append_jsonl(
                self.error_log_path,
                {
                    "timestamp": finished_at,
                    "event": (
                        "segment4_stage_exception"
                    ),
                    "stage": name,
                    "error": result.error,
                },
            )

            return result

    @staticmethod
    def _load_segment2_summary() -> dict[str, Any]:
        paths = [
            (
                ROOT
                / "state/decisions/"
                "segment2_summary.json"
            ),
            (
                ROOT
                / "state/decisions/"
                "latest_summary.json"
            ),
            (
                ROOT
                / "state/decisions/"
                "latest_shortlist.json"
            ),
        ]

        for path in paths:
            payload = read_json(
                path,
                default=None,
            )

            if isinstance(
                payload,
                dict,
            ):
                return payload

        return {}

    @staticmethod
    def _load_segment3_status() -> dict[str, Any]:
        payload = read_json(
            (
                ROOT
                / "state/execution/"
                "segment3_status.json"
            ),
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        return payload

    @staticmethod
    def _extract_counts(
        segment2: dict[str, Any],
        segment3: dict[str, Any],
    ) -> dict[str, int]:
        shortlist = segment2.get(
            "shortlist",
            [],
        )

        if isinstance(
            shortlist,
            list,
        ):
            shortlist_count = len(
                shortlist
            )
        else:
            shortlist_count = int(
                segment2.get(
                    "shortlist_count",
                    0,
                )
                or 0
            )

        evaluated = int(
            segment3.get(
                "evaluated_candidates",
                0,
            )
            or 0
        )

        approved = int(
            segment3.get(
                "risk_approved_candidates",
                0,
            )
            or 0
        )

        submitted = int(
            segment3.get(
                "submitted_orders",
                0,
            )
            or 0
        )

        return {
            "shortlist_count": (
                shortlist_count
            ),
            "evaluated_candidates": (
                evaluated
            ),
            "risk_approved_candidates": (
                approved
            ),
            "orders_submitted": (
                submitted
            ),
        }

    def _stage_plan(
        self,
    ) -> list[
        tuple[
            str,
            list[str],
        ]
    ]:
        python = sys.executable

        run_foundation = bool(
            self.config.get(
                "run_foundation_every_cycle",
                True,
            )
        )

        run_segment2 = bool(
            self.config.get(
                "run_segment2_every_cycle",
                True,
            )
        )

        run_segment3 = bool(
            self.config.get(
                "run_segment3_every_cycle",
                True,
            )
        )

        refresh_with_segment2 = bool(
            self.config.get(
                "refresh_foundation_with_segment2",
                True,
            )
        )

        stages: list[
            tuple[
                str,
                list[str],
            ]
        ] = []

        if run_foundation and not (
            run_segment2
            and refresh_with_segment2
        ):
            stages.append(
                (
                    "segment1_foundation",
                    [
                        python,
                        "run_foundation_snapshot.py",
                    ],
                )
            )

        if run_segment2:
            command = [
                python,
                "run_segment2.py",
            ]

            if refresh_with_segment2:
                command.append(
                    "--refresh-foundation"
                )

            stages.append(
                (
                    "segment2_alliance",
                    command,
                )
            )

        elif run_foundation:
            stages.append(
                (
                    "segment1_foundation",
                    [
                        python,
                        "run_foundation_snapshot.py",
                    ],
                )
            )

        if run_segment3:
            stages.append(
                (
                    "segment3_risk_preview",
                    [
                        python,
                        "run_segment3.py",
                    ],
                )
            )

        return stages

    def run(
        self,
        *,
        cycle_number: int,
        force_session: bool = False,
    ) -> CycleResult:
        cycle_id = uuid.uuid4().hex[
            :20
        ]

        started_at = utc_now_iso()
        started_monotonic = time.monotonic()

        append_jsonl(
            self.event_log_path,
            {
                "timestamp": started_at,
                "event": "segment4_cycle_started",
                "cycle_id": cycle_id,
                "cycle_number": cycle_number,
            },
        )

        safety = self.safety_gate.evaluate()

        if not safety.allowed:
            finished_at = utc_now_iso()

            result = CycleResult(
                cycle_id=cycle_id,
                cycle_number=cycle_number,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(
                    (
                        time.monotonic()
                        - started_monotonic
                    ),
                    4,
                ),
                session_allowed=False,
                session_reason=(
                    "runtime_safety_block"
                ),
                stages=[],
                successful=False,
                orders_submitted=0,
                evaluated_candidates=0,
                risk_approved_candidates=0,
                shortlist_count=0,
                error=(
                    "runtime_safety_block:"
                    + ",".join(
                        safety.reasons
                    )
                ),
                metadata={
                    "safety": safety.to_dict(),
                },
            )

            append_jsonl(
                self.cycle_log_path,
                result.to_dict(),
            )

            append_jsonl(
                self.error_log_path,
                {
                    "timestamp": finished_at,
                    "event": (
                        "segment4_cycle_safety_block"
                    ),
                    "cycle_id": cycle_id,
                    "cycle_number": cycle_number,
                    "reasons": safety.reasons,
                },
            )

            return result

        session: SessionDecision = (
            self.session_manager.evaluate()
        )

        if (
            not session.allowed
            and not force_session
        ):
            finished_at = utc_now_iso()

            result = CycleResult(
                cycle_id=cycle_id,
                cycle_number=cycle_number,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(
                    (
                        time.monotonic()
                        - started_monotonic
                    ),
                    4,
                ),
                session_allowed=False,
                session_reason=session.reason,
                stages=[],
                successful=True,
                orders_submitted=0,
                evaluated_candidates=0,
                risk_approved_candidates=0,
                shortlist_count=0,
                metadata={
                    "session": (
                        session.to_dict()
                    ),
                    "safety": (
                        safety.to_dict()
                    ),
                    "cycle_skipped": True,
                },
            )

            append_jsonl(
                self.cycle_log_path,
                result.to_dict(),
            )

            append_jsonl(
                self.event_log_path,
                {
                    "timestamp": finished_at,
                    "event": (
                        "segment4_cycle_skipped"
                    ),
                    "cycle_id": cycle_id,
                    "cycle_number": cycle_number,
                    "reason": session.reason,
                },
            )

            return result

        stages: list[
            StageResult
        ] = []

        cycle_error = ""

        for (
            stage_name,
            command,
        ) in self._stage_plan():
            stage = self._run_command(
                name=stage_name,
                command=command,
            )

            stages.append(
                stage
            )

            if not stage.passed:
                cycle_error = (
                    f"stage_failed:{stage_name}:"
                    f"{stage.error}"
                )

                break

        segment2 = (
            self._load_segment2_summary()
        )

        segment3 = (
            self._load_segment3_status()
        )

        counts = self._extract_counts(
            segment2,
            segment3,
        )

        successful = (
            not cycle_error
            and all(
                stage.passed
                for stage in stages
            )
        )

        finished_at = utc_now_iso()

        result = CycleResult(
            cycle_id=cycle_id,
            cycle_number=cycle_number,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(
                (
                    time.monotonic()
                    - started_monotonic
                ),
                4,
            ),
            session_allowed=True,
            session_reason=(
                "forced_session"
                if force_session
                and not session.allowed
                else session.reason
            ),
            stages=stages,
            successful=successful,
            orders_submitted=(
                counts[
                    "orders_submitted"
                ]
            ),
            evaluated_candidates=(
                counts[
                    "evaluated_candidates"
                ]
            ),
            risk_approved_candidates=(
                counts[
                    "risk_approved_candidates"
                ]
            ),
            shortlist_count=(
                counts[
                    "shortlist_count"
                ]
            ),
            error=cycle_error,
            metadata={
                "session": (
                    session.to_dict()
                ),
                "safety": (
                    safety.to_dict()
                ),
                "automatic_execution": False,
                "practice_submission": False,
            },
        )

        append_jsonl(
            self.cycle_log_path,
            result.to_dict(),
        )

        append_jsonl(
            self.event_log_path,
            {
                "timestamp": finished_at,
                "event": (
                    "segment4_cycle_completed"
                    if successful
                    else "segment4_cycle_failed"
                ),
                "cycle_id": cycle_id,
                "cycle_number": cycle_number,
                "duration_seconds": (
                    result.duration_seconds
                ),
                "shortlist_count": (
                    result.shortlist_count
                ),
                "risk_approved_candidates": (
                    result.risk_approved_candidates
                ),
                "orders_submitted": (
                    result.orders_submitted
                ),
                "error": result.error,
            },
        )

        return result
