from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from core.common import ROOT
from segment4b.common import (
    age_minutes,
    load_segment4b_config,
    read_json,
    utc_now,
    utc_now_iso,
)
from segment4b.models import (
    ExecutionGateDecision,
)


class PracticeExecutionGate:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_segment4b_config()
        )

        activation = self.config.get(
            "activation",
            {},
        )

        if not isinstance(
            activation,
            dict,
        ):
            activation = {}

        self.activation = activation

    def _path(
        self,
        key: str,
        default: str,
    ) -> Path:
        return (
            ROOT
            / str(
                self.activation.get(
                    key,
                    default,
                )
            )
        )

    def evaluate(
        self,
    ) -> ExecutionGateDecision:
        reasons: list[str] = []
        warnings: list[str] = []

        practice_only = bool(
            self.config.get(
                "practice_only",
                True,
            )
        )

        live_allowed = bool(
            self.config.get(
                "live_environment_allowed",
                False,
            )
        )

        automatic_execution = bool(
            self.config.get(
                "automatic_execution",
                False,
            )
        )

        submission_enabled = bool(
            self.config.get(
                (
                    "practice_order_"
                    "submission_enabled"
                ),
                False,
            )
        )

        environment = str(
            self.config.get(
                "environment",
                "practice",
            )
        ).strip().lower()

        if not bool(
            self.config.get(
                "enabled",
                False,
            )
        ):
            reasons.append(
                "segment4b_disabled"
            )

        if environment != "practice":
            reasons.append(
                "environment_not_practice"
            )

        if not practice_only:
            reasons.append(
                "practice_only_guard_disabled"
            )

        if live_allowed:
            reasons.append(
                "live_environment_allowed"
            )

        if not automatic_execution:
            reasons.append(
                "automatic_execution_disabled"
            )

        if not submission_enabled:
            reasons.append(
                "practice_order_submission_disabled"
            )

        activation_file = self._path(
            "activation_file",
            (
                "state/execution4b/"
                "PRACTICE_EXECUTION_ENABLED"
            ),
        )

        approval_file = self._path(
            "approval_file",
            (
                "state/execution4b/"
                "MANUAL_APPROVAL.json"
            ),
        )

        kill_switch = self._path(
            "kill_switch_file",
            "state/SEGMENT3_KILL_SWITCH",
        )

        pause_file = self._path(
            "segment3_pause_file",
            (
                "state/execution/"
                "segment3_pause.json"
            ),
        )

        unresolved_recovery = self._path(
            "unresolved_recovery_file",
            (
                "state/recovery/"
                "UNRESOLVED_RECOVERY"
            ),
        )

        if not activation_file.exists():
            reasons.append(
                "activation_file_missing"
            )

        if not approval_file.exists():
            reasons.append(
                "manual_approval_missing"
            )

            approval: dict[str, Any] = {}

        else:
            payload = read_json(
                approval_file,
                default={},
            )

            approval = (
                payload
                if isinstance(
                    payload,
                    dict,
                )
                else {}
            )

            required_phrase = str(
                self.activation.get(
                    "required_phrase",
                    (
                        "ENABLE OANDA "
                        "PRACTICE EXECUTION"
                    ),
                )
            )

            exact_required = bool(
                self.activation.get(
                    "require_exact_phrase",
                    True,
                )
            )

            supplied_phrase = str(
                approval.get(
                    "approval_phrase",
                    "",
                )
            )

            phrase_matches = (
                supplied_phrase
                == required_phrase
                if exact_required
                else (
                    supplied_phrase.strip().lower()
                    == required_phrase.strip().lower()
                )
            )

            if not phrase_matches:
                reasons.append(
                    "manual_approval_phrase_invalid"
                )

            approval_age = age_minutes(
                approval.get(
                    "approved_at"
                )
            )

            maximum_age = float(
                self.activation.get(
                    (
                        "maximum_approval_"
                        "age_minutes"
                    ),
                    30,
                )
            )

            if approval_age is None:
                reasons.append(
                    "manual_approval_timestamp_invalid"
                )

            elif approval_age > maximum_age:
                reasons.append(
                    "manual_approval_expired"
                )

        if kill_switch.exists():
            reasons.append(
                "segment3_kill_switch_active"
            )

        if pause_file.exists():
            reasons.append(
                "segment3_pause_active"
            )

        if unresolved_recovery.exists():
            reasons.append(
                "unresolved_recovery_active"
            )

        allowed = (
            len(
                reasons
            )
            == 0
        )

        return ExecutionGateDecision(
            timestamp=utc_now_iso(),
            allowed=allowed,
            mode=(
                "practice_execution"
                if allowed
                else "blocked"
            ),
            reasons=reasons,
            warnings=warnings,
            metadata={
                "environment": environment,
                "activation_file": str(
                    activation_file
                ),
                "approval_file": str(
                    approval_file
                ),
                "approval": approval,
                "kill_switch": (
                    kill_switch.exists()
                ),
                "segment3_pause": (
                    pause_file.exists()
                ),
                "unresolved_recovery": (
                    unresolved_recovery.exists()
                ),
            },
            automatic_execution=(
                automatic_execution
            ),
            practice_order_submission_enabled=(
                submission_enabled
            ),
        )
