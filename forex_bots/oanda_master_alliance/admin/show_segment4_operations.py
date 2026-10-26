from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.common import (
    read_json,
)
from segment4.operations_common import (
    operations_paths,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Display Segment 4A-2 "
            "operations state"
        )
    )

    parser.add_argument(
        "view",
        choices=[
            "summary",
            "health",
            "recovery",
            "watchdog",
            "duplicates",
            "restarts",
        ],
        default="summary",
        nargs="?",
    )

    arguments = parser.parse_args()
    paths = operations_paths()

    mapping = {
        "health": (
            "health_status"
        ),
        "recovery": (
            "recovery_status"
        ),
        "watchdog": (
            "watchdog_status"
        ),
        "duplicates": (
            "duplicate_registry"
        ),
        "restarts": (
            "restart_history"
        ),
    }

    if arguments.view != "summary":
        path = paths[
            mapping[
                arguments.view
            ]
        ]

        if not path.exists():
            raise SystemExit(
                f"{path} has not been created"
            )

        print(
            json.dumps(
                read_json(
                    path,
                    default={},
                ),
                indent=2,
            )
        )

        return

    health = read_json(
        paths[
            "health_status"
        ],
        default={},
    )

    recovery = read_json(
        paths[
            "recovery_status"
        ],
        default={},
    )

    watchdog = read_json(
        paths[
            "watchdog_status"
        ],
        default={},
    )

    if not isinstance(
        health,
        dict,
    ):
        health = {}

    if not isinstance(
        recovery,
        dict,
    ):
        recovery = {}

    if not isinstance(
        watchdog,
        dict,
    ):
        watchdog = {}

    print()
    print(
        "OANDA MASTER AI — SEGMENT 4A-2"
    )
    print("=" * 80)

    print(
        "Health:                 "
        f"{health.get('healthy', 'not_run')}"
    )

    print(
        "Critical failures:      "
        f"{health.get('critical_failures', 0)}"
    )

    print(
        "Warnings:               "
        f"{health.get('warnings', 0)}"
    )

    print(
        "Recovery passed:        "
        f"{recovery.get('recovery_passed', 'not_run')}"
    )

    print(
        "Recovery blockers:      "
        f"{recovery.get('unresolved_blockers', 0)}"
    )

    print(
        "Watchdog state:         "
        f"{watchdog.get('state', 'not_run')}"
    )

    print(
        "Watchdog running:       "
        f"{watchdog.get('watchdog_running', False)}"
    )

    print(
        "Restart action:         "
        f"{watchdog.get('action', 'none')}"
    )

    print(
        "Unresolved recovery:    "
        f"{paths['unresolved_recovery'].exists()}"
    )

    print(
        "Automatic execution:    False"
    )

    print(
        "Order submission:       False"
    )


if __name__ == "__main__":
    main()
