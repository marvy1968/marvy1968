from __future__ import annotations

from typing import Any

from segment4.operations_common import (
    load_operations_config,
    operations_paths,
)
from segment4.recovery import (
    RecoveryManager,
)


class StartupGuard:
    def __init__(
        self,
    ) -> None:
        self.config = (
            load_operations_config()
        )

        self.paths = operations_paths()

    def evaluate(
        self,
    ) -> dict[str, Any]:
        recovery_enabled = bool(
            self.config.get(
                "recovery_enabled",
                True,
            )
        )

        if not recovery_enabled:
            return {
                "allowed": True,
                "reason": (
                    "recovery_disabled"
                ),
                "recovery": None,
            }

        report = (
            RecoveryManager().run()
        )

        block = bool(
            self.config.get(
                (
                    "block_on_unresolved_"
                    "recovery"
                ),
                True,
            )
        )

        allowed = (
            report.recovery_passed
            or not block
        )

        return {
            "allowed": allowed,
            "reason": (
                "recovery_passed"
                if allowed
                else "unresolved_recovery"
            ),
            "recovery": report.to_dict(),
        }
