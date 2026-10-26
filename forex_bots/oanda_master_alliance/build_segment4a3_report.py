from __future__ import annotations

import json
import subprocess
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


from segment4.deployment_common import (
    deployment_paths,
    load_segment4a3_config,
    read_json,
    safety_configuration,
    utc_now_iso,
)


def systemctl_state(
    unit: str,
) -> dict[str, Any]:
    result: dict[
        str,
        Any
    ] = {
        "unit": unit,
        "active": "unknown",
        "enabled": "unknown",
    }

    try:
        active = subprocess.run(
            [
                "systemctl",
                "is-active",
                unit,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

        enabled = subprocess.run(
            [
                "systemctl",
                "is-enabled",
                unit,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

        result[
            "active"
        ] = (
            active.stdout.strip()
            or active.stderr.strip()
            or "unknown"
        )

        result[
            "enabled"
        ] = (
            enabled.stdout.strip()
            or enabled.stderr.strip()
            or "unknown"
        )

    except (
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        result[
            "error"
        ] = str(
            exc
        )

    return result


def main() -> None:
    config = load_segment4a3_config()
    paths = deployment_paths()
    safety = safety_configuration()

    backup = read_json(
        paths[
            "backup_status"
        ],
        default={},
    )

    maintenance = read_json(
        paths[
            "maintenance_status"
        ],
        default={},
    )

    alert = read_json(
        paths[
            "alert_status"
        ],
        default={},
    )

    deployment = read_json(
        paths[
            "deployment_status"
        ],
        default={},
    )

    services = [
        "oanda-master-ai.service",
        "oanda-master-ai-watchdog.service",
        "oanda-master-ai-health.timer",
        "oanda-master-ai-maintenance.timer",
        "oanda-master-ai-backup.timer",
        "oanda-master-ai-report.timer",
    ]

    service_states = [
        systemctl_state(
            unit
        )
        for unit in services
    ]

    lines = [
        (
            "# OANDA Master AI — "
            "Segment 4A-3"
        ),
        "",
        "## Deployment",
        "",
        (
            "- Generated: "
            f"**{utc_now_iso()}**"
        ),
        (
            "- Deployment mode: "
            f"**{config.get('deployment_mode')}**"
        ),
        (
            "- Installer completed: "
            f"**{deployment.get('installed', False)}**"
        ),
        (
            "- Systemd units copied: "
            f"**{deployment.get('systemd_units_installed', False)}**"
        ),
        (
            "- Services automatically enabled: "
            "**False**"
        ),
        "",
        "## Safety",
        "",
        (
            "- Safety passed: "
            f"**{safety.get('passed')}**"
        ),
        (
            "- Practice only: "
            f"**{config.get('practice_only')}**"
        ),
        (
            "- Live environment allowed: "
            f"**{config.get('live_environment_allowed')}**"
        ),
        (
            "- Automatic execution: "
            f"**{config.get('automatic_execution')}**"
        ),
        (
            "- Practice-order submission: "
            f"**{config.get('practice_order_submission_enabled')}**"
        ),
        "",
        "## Services and timers",
        "",
        "| Unit | Active | Enabled |",
        "|---|---|---|",
    ]

    for state in service_states:
        lines.append(
            "| "
            f"{state.get('unit')} | "
            f"{state.get('active')} | "
            f"{state.get('enabled')} |"
        )

    if not isinstance(
        backup,
        dict,
    ):
        backup = {}

    if not isinstance(
        maintenance,
        dict,
    ):
        maintenance = {}

    if not isinstance(
        alert,
        dict,
    ):
        alert = {}

    lines.extend(
        [
            "",
            "## Latest backup",
            "",
            (
                "- Successful: "
                f"**{backup.get('successful', 'not_run')}**"
            ),
            (
                "- Archive: "
                f"**{backup.get('archive') or 'none'}**"
            ),
            (
                "- Size: "
                f"**{backup.get('archive_size_bytes', 0)} bytes**"
            ),
            (
                "- Error: "
                f"**{backup.get('error') or 'none'}**"
            ),
            "",
            "## Latest maintenance",
            "",
            (
                "- Successful: "
                f"**{maintenance.get('successful', 'not_run')}**"
            ),
            (
                "- Actions: "
                f"**{len(maintenance.get('actions', [])) if isinstance(maintenance.get('actions'), list) else 0}**"
            ),
            (
                "- Free disk: "
                f"**{maintenance.get('free_disk_mb', 'unknown')} MB**"
            ),
            "",
            "## Latest alert",
            "",
            (
                "- Event: "
                f"**{alert.get('event_type', 'none')}**"
            ),
            (
                "- Severity: "
                f"**{alert.get('severity', 'none')}**"
            ),
            (
                "- Delivered: "
                f"**{alert.get('delivered', False)}**"
            ),
            (
                "- Suppressed: "
                f"**{alert.get('suppressed', False)}**"
            ),
            "",
            "## Operational guardrails",
            "",
            (
                "- Systemd installation does not "
                "enable the daemon automatically."
            ),
            (
                "- Automatic restart inside the "
                "watchdog remains disabled."
            ),
            (
                "- The main systemd service uses "
                "on-failure restart with a burst limit."
            ),
            (
                "- Recovery validation can block "
                "unsafe runtime startup."
            ),
            (
                "- Credentials are excluded from "
                "application backups."
            ),
            (
                "- Backups include checksums and "
                "archive validation."
            ),
            (
                "- Alerts do not authorize or "
                "submit trades."
            ),
            (
                "- Automatic execution and order "
                "submission remain disabled."
            ),
            "",
        ]
    )

    output = paths[
        "report"
    ]

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
