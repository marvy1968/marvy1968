from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


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


from segment4.common import (
    read_json,
)
from segment4.operations_common import (
    load_operations_config,
    operations_paths,
)


def check_rows(
    payload: dict[str, Any],
) -> list[str]:
    rows: list[str] = []

    checks = payload.get(
        "checks",
        [],
    )

    if not isinstance(
        checks,
        list,
    ):
        return rows

    for check in checks:
        if not isinstance(
            check,
            dict,
        ):
            continue

        rows.append(
            "| "
            + str(
                check.get(
                    "name",
                    "unknown",
                )
            )
            + " | "
            + str(
                check.get(
                    "passed",
                    False,
                )
            )
            + " | "
            + str(
                check.get(
                    "severity",
                    check.get(
                        "blocking",
                        "",
                    ),
                )
            )
            + " | "
            + str(
                check.get(
                    "message",
                    "",
                )
            ).replace(
                "|",
                "/",
            )
            + " |"
        )

    return rows


def main() -> None:
    config = load_operations_config()
    paths = operations_paths()

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

    duplicate_registry = read_json(
        paths[
            "duplicate_registry"
        ],
        default={},
    )

    restart_history = read_json(
        paths[
            "restart_history"
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

    if not isinstance(
        duplicate_registry,
        dict,
    ):
        duplicate_registry = {}

    if not isinstance(
        restart_history,
        dict,
    ):
        restart_history = {}

    lines = [
        (
            "# OANDA Master AI — "
            "Segment 4A-2"
        ),
        "",
        (
            "## Operations configuration"
        ),
        "",
        (
            "- Watchdog enabled: "
            f"**{config.get('watchdog_enabled')}**"
        ),
        (
            "- Automatic restart enabled: "
            f"**{config.get('automatic_restart_enabled')}**"
        ),
        (
            "- Recovery enabled: "
            f"**{config.get('recovery_enabled')}**"
        ),
        (
            "- Block unresolved recovery: "
            f"**{config.get('block_on_unresolved_recovery')}**"
        ),
        (
            "- Automatic execution: "
            "**False**"
        ),
        (
            "- Order submission: "
            "**False**"
        ),
        "",
        "## Watchdog",
        "",
        (
            "- State: "
            f"**{watchdog.get('state', 'not_run')}**"
        ),
        (
            "- Watchdog running: "
            f"**{watchdog.get('watchdog_running', False)}**"
        ),
        (
            "- Main process running: "
            f"**{watchdog.get('main_process_running', False)}**"
        ),
        (
            "- Heartbeat fresh: "
            f"**{watchdog.get('heartbeat_fresh')}**"
        ),
        (
            "- Action: "
            f"**{watchdog.get('action', 'none')}**"
        ),
        (
            "- Action reason: "
            f"**{watchdog.get('action_reason') or 'none'}**"
        ),
        "",
        "## Health",
        "",
        (
            "- Healthy: "
            f"**{health.get('healthy', False)}**"
        ),
        (
            "- Critical failures: "
            f"**{health.get('critical_failures', 0)}**"
        ),
        (
            "- Warnings: "
            f"**{health.get('warnings', 0)}**"
        ),
        "",
        "| Check | Passed | Severity | Message |",
        "|---|---:|---|---|",
    ]

    health_rows = check_rows(
        health
    )

    if health_rows:
        lines.extend(
            health_rows
        )
    else:
        lines.append(
            "| None | — | — | Health monitor has not run. |"
        )

    lines.extend(
        [
            "",
            "## Recovery",
            "",
            (
                "- Recovery required: "
                f"**{recovery.get('recovery_required', False)}**"
            ),
            (
                "- Recovery passed: "
                f"**{recovery.get('recovery_passed', False)}**"
            ),
            (
                "- Unresolved blockers: "
                f"**{recovery.get('unresolved_blockers', 0)}**"
            ),
            (
                "- Open positions recorded: "
                f"**{recovery.get('open_positions', 0)}**"
            ),
            (
                "- Pending orders recorded: "
                f"**{recovery.get('pending_orders', 0)}**"
            ),
            (
                "- Unresolved-recovery file: "
                f"**{paths['unresolved_recovery'].exists()}**"
            ),
            "",
            "| Check | Passed | Blocking | Message |",
            "|---|---:|---|---|",
        ]
    )

    recovery_rows = check_rows(
        recovery
    )

    if recovery_rows:
        lines.extend(
            recovery_rows
        )
    else:
        lines.append(
            "| None | — | — | Recovery manager has not run. |"
        )

    signals = duplicate_registry.get(
        "signals",
        {},
    )

    authorizations = (
        duplicate_registry.get(
            "authorizations",
            {},
        )
    )

    orders = duplicate_registry.get(
        "orders",
        {},
    )

    attempts = restart_history.get(
        "attempts",
        [],
    )

    lines.extend(
        [
            "",
            "## Duplicate protection",
            "",
            (
                "- Registered signals: "
                f"**{len(signals) if isinstance(signals, dict) else 0}**"
            ),
            (
                "- Registered authorizations: "
                f"**{len(authorizations) if isinstance(authorizations, dict) else 0}**"
            ),
            (
                "- Registered orders: "
                f"**{len(orders) if isinstance(orders, dict) else 0}**"
            ),
            "",
            "## Restart history",
            "",
            (
                "- Restart attempts in stored history: "
                f"**{len(attempts) if isinstance(attempts, list) else 0}**"
            ),
            (
                "- Automatic restart remains: "
                f"**{'enabled' if config.get('automatic_restart_enabled') else 'disabled'}**"
            ),
            "",
            "## Guardrails",
            "",
            (
                "- Watchdog health checks do not "
                "authorize trades."
            ),
            (
                "- Automatic restart is disabled "
                "by default."
            ),
            (
                "- Unresolved recovery can block "
                "daemon startup."
            ),
            (
                "- Segment 3 kill switch and pause "
                "remain authoritative."
            ),
            (
                "- Duplicate protection tracks signals, "
                "authorizations and orders."
            ),
            (
                "- Practice-only mode remains enforced."
            ),
            (
                "- Automatic execution and order "
                "submission remain disabled."
            ),
            "",
        ]
    )

    output = (
        ROOT
        / "reports/segment4/"
        "segment4a2_report.md"
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )

    print(
        "\n".join(
            lines
        )
    )


if __name__ == "__main__":
    main()
