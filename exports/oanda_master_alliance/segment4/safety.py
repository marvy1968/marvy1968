from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from segment4.common import (
    ROOT,
    read_json,
)


@dataclass
class SafetyDecision:
    allowed: bool
    reasons: list[str]
    warnings: list[str]
    metadata: dict[str, Any]

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


class RuntimeSafetyGate:
    def __init__(
        self,
        config: dict,
    ) -> None:
        self.config = config
        self.safety = dict(
            config.get(
                "safety",
                {},
            )
        )

    @staticmethod
    def _load_segment3_config() -> dict[str, Any]:
        path = (
            ROOT
            / "config/segment3.yaml"
        )

        if not path.exists():
            return {}

        payload = yaml.safe_load(
            path.read_text(
                encoding="utf-8",
            )
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        segment3 = payload.get(
            "segment3",
            {},
        )

        if not isinstance(
            segment3,
            dict,
        ):
            return {}

        return segment3

    def evaluate(
        self,
    ) -> SafetyDecision:
        reasons: list[str] = []
        warnings: list[str] = []

        segment3_config = (
            self._load_segment3_config()
        )

        segment3_status_path = (
            ROOT
            / "state/execution/"
            "segment3_status.json"
        )

        segment3_status = read_json(
            segment3_status_path,
            default={},
        )

        if not isinstance(
            segment3_status,
            dict,
        ):
            segment3_status = {}

        segment3_kill_switch = (
            ROOT
            / "state/SEGMENT3_KILL_SWITCH"
        )

        segment3_pause = (
            ROOT
            / "state/execution/"
            "segment3_pause.json"
        )

        practice_only = bool(
            segment3_config.get(
                "practice_only",
                True,
            )
        )

        automatic_execution = bool(
            segment3_config.get(
                "automatic_execution",
                False,
            )
        )

        submission_enabled = bool(
            segment3_config.get(
                "practice_order_submission_enabled",
                False,
            )
        )

        if bool(
            self.safety.get(
                "require_segment3_practice_only",
                True,
            )
        ) and not practice_only:
            reasons.append(
                "segment3_not_practice_only"
            )

        if bool(
            self.safety.get(
                "require_segment3_execution_disabled",
                True,
            )
        ) and automatic_execution:
            reasons.append(
                "segment3_automatic_execution_enabled"
            )

        if bool(
            self.safety.get(
                "require_segment3_submission_disabled",
                True,
            )
        ) and submission_enabled:
            reasons.append(
                "segment3_order_submission_enabled"
            )

        if bool(
            self.safety.get(
                "block_when_segment3_kill_switch_enabled",
                True,
            )
        ) and segment3_kill_switch.exists():
            reasons.append(
                "segment3_kill_switch_enabled"
            )

        if bool(
            self.safety.get(
                "block_when_segment3_paused",
                True,
            )
        ) and segment3_pause.exists():
            reasons.append(
                "segment3_risk_pause_active"
            )

        environment = str(
            segment3_status.get(
                "environment",
                "practice",
            )
        ).lower()

        if environment == "live":
            reasons.append(
                "live_environment_detected"
            )

        if not segment3_status_path.exists():
            warnings.append(
                "segment3_status_not_created_yet"
            )

        return SafetyDecision(
            allowed=(
                len(
                    reasons
                )
                == 0
            ),
            reasons=reasons,
            warnings=warnings,
            metadata={
                "segment3_practice_only": practice_only,
                "segment3_automatic_execution": (
                    automatic_execution
                ),
                "segment3_submission_enabled": (
                    submission_enabled
                ),
                "segment3_kill_switch": (
                    segment3_kill_switch.exists()
                ),
                "segment3_paused": (
                    segment3_pause.exists()
                ),
                "segment3_environment": environment,
            },
        )
