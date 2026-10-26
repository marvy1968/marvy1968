from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
    field,
)
from typing import Any


@dataclass
class StageResult:
    name: str
    attempted: bool
    passed: bool
    started_at: str
    finished_at: str
    duration_seconds: float
    return_code: int | None = None
    command: list[str] = field(
        default_factory=list
    )
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class CycleResult:
    cycle_id: str
    cycle_number: int
    started_at: str
    finished_at: str
    duration_seconds: float
    session_allowed: bool
    session_reason: str
    stages: list[StageResult]
    successful: bool
    orders_submitted: int
    evaluated_candidates: int
    risk_approved_candidates: int
    shortlist_count: int
    error: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        payload = asdict(
            self
        )

        payload["stages"] = [
            stage.to_dict()
            for stage in self.stages
        ]

        return payload
