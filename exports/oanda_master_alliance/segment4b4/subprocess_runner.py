from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

from segment4b4.common import (
    ROOT,
    utc_now_iso,
)


@dataclass(slots=True)
class StageResult:
    name: str
    attempted: bool
    successful: bool
    command: list[str]
    started_at: str
    finished_at: str
    duration_seconds: float
    return_code: int
    stdout_tail: str
    stderr_tail: str
    error: str
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SafeStageRunner:
    def __init__(
        self,
        *,
        timeout_seconds: int = 600,
        tail_characters: int = 12000,
    ) -> None:
        self.timeout_seconds = max(
            1,
            int(timeout_seconds),
        )

        self.tail_characters = max(
            1000,
            int(tail_characters),
        )

    def run_python(
        self,
        name: str,
        script: str,
        *arguments: str,
    ) -> StageResult:
        command = [
            sys.executable,
            script,
            *arguments,
        ]

        started_at = utc_now_iso()
        started_monotonic = time.monotonic()

        try:
            completed = subprocess.run(
                command,
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )

            successful = (
                completed.returncode == 0
            )

            error = ""

            if not successful:
                error = (
                    f"{name} returned "
                    f"exit code "
                    f"{completed.returncode}"
                )

            return StageResult(
                name=name,
                attempted=True,
                successful=successful,
                command=command,
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_seconds=round(
                    time.monotonic()
                    - started_monotonic,
                    4,
                ),
                return_code=(
                    completed.returncode
                ),
                stdout_tail=(
                    completed.stdout[
                        -self.tail_characters:
                    ]
                ),
                stderr_tail=(
                    completed.stderr[
                        -self.tail_characters:
                    ]
                ),
                error=error,
            )

        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout
                if isinstance(
                    exc.stdout,
                    str,
                )
                else ""
            )

            stderr = (
                exc.stderr
                if isinstance(
                    exc.stderr,
                    str,
                )
                else ""
            )

            return StageResult(
                name=name,
                attempted=True,
                successful=False,
                command=command,
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_seconds=round(
                    time.monotonic()
                    - started_monotonic,
                    4,
                ),
                return_code=124,
                stdout_tail=stdout[
                    -self.tail_characters:
                ],
                stderr_tail=stderr[
                    -self.tail_characters:
                ],
                error=(
                    f"{name} timed out after "
                    f"{self.timeout_seconds} "
                    "seconds"
                ),
            )

        except Exception as exc:
            return StageResult(
                name=name,
                attempted=True,
                successful=False,
                command=command,
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_seconds=round(
                    time.monotonic()
                    - started_monotonic,
                    4,
                ),
                return_code=1,
                stdout_tail="",
                stderr_tail="",
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )
