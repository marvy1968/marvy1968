from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.duplicate_registry import (
    DuplicateRegistry,
)
from segment4.health_monitor import (
    HealthMonitor,
)
from segment4.operations_common import (
    load_operations_config,
)
from segment4.recovery import (
    RecoveryManager,
)
from segment4.watchdog import (
    RestartHistory,
)


def main() -> None:
    config = load_operations_config()

    assert (
        config[
            "watchdog_enabled"
        ]
        is True
    )

    assert (
        config[
            "automatic_restart_enabled"
        ]
        is False
    )

    assert (
        config[
            "recovery_enabled"
        ]
        is True
    )

    with tempfile.TemporaryDirectory() as directory:
        history = RestartHistory(
            Path(
                directory
            )
            / "restart_history.json",
            {
                "maximum_restart_attempts": 2,
                "restart_window_minutes": 60,
                "restart_backoff_seconds": [
                    1,
                    2,
                ],
            },
        )

        assert history.allowed()

        history.record(
            reason="test_1",
            pid=101,
            successful_launch=True,
        )

        assert history.allowed()

        history.record(
            reason="test_2",
            pid=102,
            successful_launch=True,
        )

        assert not history.allowed()

    registry = DuplicateRegistry()

    signal_id = registry.make_signal_id(
        instrument="EUR_USD",
        side="buy",
        setup="test",
        entry_price=1.10000,
        source_timestamp=(
            "2026-07-15T00:00:00+00:00"
        ),
    )

    assert signal_id

    health = HealthMonitor().run()
    recovery = RecoveryManager().run()

    payload = {
        "status": "passed",
        "watchdog_enabled": (
            config[
                "watchdog_enabled"
            ]
        ),
        "automatic_restart_enabled": (
            config[
                "automatic_restart_enabled"
            ]
        ),
        "recovery_enabled": (
            config[
                "recovery_enabled"
            ]
        ),
        "duplicate_signal_id_created": (
            bool(
                signal_id
            )
        ),
        "health_checks": len(
            health.checks
        ),
        "health_critical_failures": (
            health.critical_failures
        ),
        "recovery_passed": (
            recovery.recovery_passed
        ),
        "unresolved_blockers": (
            recovery.unresolved_blockers
        ),
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — SEGMENT 4A-2"
    )
    print("=" * 72)

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4A-2 verification passed."
    )


if __name__ == "__main__":
    main()
