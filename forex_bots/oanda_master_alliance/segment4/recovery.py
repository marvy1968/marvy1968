from __future__ import annotations

import json
from dataclasses import (
    asdict,
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

from segment4.common import (
    ROOT,
    append_jsonl,
    atomic_write_json,
    read_json,
    utc_now_iso,
)
from segment4.duplicate_registry import (
    DuplicateRegistry,
)
from segment4.operations_common import (
    load_operations_config,
    operations_paths,
    write_checkpoint,
)


@dataclass
class RecoveryCheck:
    name: str
    passed: bool
    blocking: bool
    message: str
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
class RecoveryReport:
    timestamp: str
    recovery_required: bool
    recovery_passed: bool
    unresolved_blockers: int
    checks: list[RecoveryCheck]
    open_positions: int
    pending_orders: int
    duplicate_registry: dict[str, Any]
    automatic_execution: bool
    order_submission: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "recovery_required": (
                self.recovery_required
            ),
            "recovery_passed": (
                self.recovery_passed
            ),
            "unresolved_blockers": (
                self.unresolved_blockers
            ),
            "checks": [
                check.to_dict()
                for check in self.checks
            ],
            "open_positions": (
                self.open_positions
            ),
            "pending_orders": (
                self.pending_orders
            ),
            "duplicate_registry": (
                self.duplicate_registry
            ),
            "automatic_execution": (
                self.automatic_execution
            ),
            "order_submission": (
                self.order_submission
            ),
        }


class RecoveryManager:
    def __init__(
        self,
    ) -> None:
        self.config = (
            load_operations_config()
        )

        self.paths = operations_paths()

        self.registry = (
            DuplicateRegistry()
        )

        self.segment4_status = (
            ROOT
            / "state/segment4/status.json"
        )

        self.segment3_status = (
            ROOT
            / "state/execution/"
            "segment3_status.json"
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

        self.open_positions_file = (
            ROOT
            / "state/execution/"
            "open_positions.json"
        )

        self.pending_orders_file = (
            ROOT
            / "state/execution/"
            "pending_orders.json"
        )

    @staticmethod
    def _extract_list(
        payload: Any,
        *keys: str,
    ) -> list[Any]:
        if isinstance(
            payload,
            list,
        ):
            return payload

        if not isinstance(
            payload,
            dict,
        ):
            return []

        for key in keys:
            value = payload.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return value

        return []

    def _open_positions(
        self,
    ) -> list[Any]:
        payload = read_json(
            self.open_positions_file,
            default=[],
        )

        return self._extract_list(
            payload,
            "positions",
            "open_positions",
        )

    def _pending_orders(
        self,
    ) -> list[Any]:
        payload = read_json(
            self.pending_orders_file,
            default=[],
        )

        return self._extract_list(
            payload,
            "orders",
            "pending_orders",
        )

    def _check_prior_shutdown(
        self,
    ) -> RecoveryCheck:
        status = read_json(
            self.segment4_status,
            default={},
        )

        if not isinstance(
            status,
            dict,
        ):
            status = {}

        prior_running = bool(
            status.get(
                "daemon_running",
                False,
            )
        )

        prior_state = str(
            status.get(
                "state",
                "unknown",
            )
        )

        passed = (
            not prior_running
            or prior_state
            in {
                "stopped",
                "installed_not_started",
            }
        )

        return RecoveryCheck(
            name="prior_shutdown_state",
            passed=passed,
            blocking=False,
            message=(
                "Prior runtime ended cleanly."
                if passed
                else (
                    "Prior runtime may not have "
                    "ended cleanly."
                )
            ),
            metadata={
                "prior_running": prior_running,
                "prior_state": prior_state,
            },
        )

    def _check_segment3_safety(
        self,
    ) -> RecoveryCheck:
        status = read_json(
            self.segment3_status,
            default={},
        )

        if not isinstance(
            status,
            dict,
        ):
            status = {}

        practice_only = bool(
            status.get(
                "practice_only",
                True,
            )
        )

        automatic_execution = bool(
            status.get(
                "automatic_execution",
                False,
            )
        )

        submission_enabled = bool(
            status.get(
                (
                    "practice_order_"
                    "submission_enabled"
                ),
                False,
            )
        )

        passed = (
            practice_only
            and not automatic_execution
            and not submission_enabled
        )

        return RecoveryCheck(
            name="segment3_safety",
            passed=passed,
            blocking=True,
            message=(
                "Segment 3 safety settings passed."
                if passed
                else (
                    "Segment 3 safety settings "
                    "failed recovery validation."
                )
            ),
            metadata={
                "practice_only": practice_only,
                "automatic_execution": (
                    automatic_execution
                ),
                "submission_enabled": (
                    submission_enabled
                ),
                "kill_switch": (
                    self.segment3_kill_switch
                    .exists()
                ),
                "paused": (
                    self.segment3_pause
                    .exists()
                ),
            },
        )

    def _check_positions(
        self,
        positions: list[Any],
    ) -> RecoveryCheck:
        reconcile = bool(
            self.config.get(
                "reconcile_open_positions",
                True,
            )
        )

        if not reconcile:
            return RecoveryCheck(
                name="open_position_reconciliation",
                passed=True,
                blocking=False,
                message=(
                    "Open-position reconciliation "
                    "is disabled."
                ),
                metadata={
                    "position_count": len(
                        positions
                    ),
                },
            )

        malformed = [
            position
            for position in positions
            if not isinstance(
                position,
                dict,
            )
            or not str(
                position.get(
                    "instrument",
                    "",
                )
            ).strip()
        ]

        passed = (
            len(
                malformed
            )
            == 0
        )

        return RecoveryCheck(
            name="open_position_reconciliation",
            passed=passed,
            blocking=True,
            message=(
                "Open-position state is structurally valid."
                if passed
                else (
                    "Open-position state contains "
                    "malformed records."
                )
            ),
            metadata={
                "position_count": len(
                    positions
                ),
                "malformed_count": len(
                    malformed
                ),
            },
        )

    def _check_orders(
        self,
        orders: list[Any],
    ) -> RecoveryCheck:
        reconcile = bool(
            self.config.get(
                "reconcile_pending_orders",
                True,
            )
        )

        if not reconcile:
            return RecoveryCheck(
                name="pending_order_reconciliation",
                passed=True,
                blocking=False,
                message=(
                    "Pending-order reconciliation "
                    "is disabled."
                ),
                metadata={
                    "order_count": len(
                        orders
                    ),
                },
            )

        malformed = [
            order
            for order in orders
            if not isinstance(
                order,
                dict,
            )
            or not str(
                order.get(
                    "instrument",
                    "",
                )
            ).strip()
        ]

        passed = (
            len(
                malformed
            )
            == 0
        )

        return RecoveryCheck(
            name="pending_order_reconciliation",
            passed=passed,
            blocking=True,
            message=(
                "Pending-order state is structurally valid."
                if passed
                else (
                    "Pending-order state contains "
                    "malformed records."
                )
            ),
            metadata={
                "order_count": len(
                    orders
                ),
                "malformed_count": len(
                    malformed
                ),
            },
        )

    def run(
        self,
    ) -> RecoveryReport:
        positions = (
            self._open_positions()
        )

        orders = (
            self._pending_orders()
        )

        checks = [
            self._check_prior_shutdown(),
            self._check_segment3_safety(),
            self._check_positions(
                positions
            ),
            self._check_orders(
                orders
            ),
        ]

        registry_summary = (
            self.registry.summary()
        )

        unresolved_blockers = sum(
            1
            for check in checks
            if not check.passed
            and check.blocking
        )

        recovery_required = any(
            not check.passed
            for check in checks
        ) or bool(
            positions
            or orders
        )

        recovery_passed = (
            unresolved_blockers
            == 0
        )

        report = RecoveryReport(
            timestamp=utc_now_iso(),
            recovery_required=(
                recovery_required
            ),
            recovery_passed=(
                recovery_passed
            ),
            unresolved_blockers=(
                unresolved_blockers
            ),
            checks=checks,
            open_positions=len(
                positions
            ),
            pending_orders=len(
                orders
            ),
            duplicate_registry=(
                registry_summary
            ),
            automatic_execution=False,
            order_submission=False,
        )

        atomic_write_json(
            self.paths[
                "recovery_status"
            ],
            report.to_dict(),
        )

        append_jsonl(
            self.paths[
                "recovery_history"
            ],
            report.to_dict(),
        )

        write_checkpoint(
            {
                "component": (
                    "segment4_recovery"
                ),
                "recovery_passed": (
                    recovery_passed
                ),
                "open_positions": len(
                    positions
                ),
                "pending_orders": len(
                    orders
                ),
                "duplicate_registry": (
                    registry_summary
                ),
            }
        )

        unresolved_path = self.paths[
            "unresolved_recovery"
        ]

        block_on_unresolved = bool(
            self.config.get(
                (
                    "block_on_unresolved_"
                    "recovery"
                ),
                True,
            )
        )

        if (
            unresolved_blockers > 0
            and block_on_unresolved
        ):
            unresolved_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            unresolved_path.write_text(
                json.dumps(
                    report.to_dict(),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        else:
            unresolved_path.unlink(
                missing_ok=True
            )

        return report
