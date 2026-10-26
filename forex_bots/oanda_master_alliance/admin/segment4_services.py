from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


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


from segment4.deployment_common import (
    atomic_write_json,
    deployment_paths,
    load_segment4a3_config,
    safety_configuration,
    utc_now_iso,
)


SERVICES = [
    "oanda-master-ai.service",
    "oanda-master-ai-watchdog.service",
    "oanda-master-ai-health.service",
    "oanda-master-ai-health.timer",
    "oanda-master-ai-recovery.service",
    "oanda-master-ai-maintenance.service",
    "oanda-master-ai-maintenance.timer",
    "oanda-master-ai-backup.service",
    "oanda-master-ai-backup.timer",
    "oanda-master-ai-report.service",
    "oanda-master-ai-report.timer",
]


def systemctl(
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "systemctl",
            *arguments,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def unit_status(
    unit: str,
) -> dict[str, Any]:
    active = systemctl(
        "is-active",
        unit,
    )

    enabled = systemctl(
        "is-enabled",
        unit,
    )

    return {
        "unit": unit,
        "active": (
            active.stdout.strip()
            or active.stderr.strip()
        ),
        "active_return_code": (
            active.returncode
        ),
        "enabled": (
            enabled.stdout.strip()
            or enabled.stderr.strip()
        ),
        "enabled_return_code": (
            enabled.returncode
        ),
    }


def status() -> dict[str, Any]:
    payload = {
        "timestamp": utc_now_iso(),
        "safety": safety_configuration(),
        "services": [
            unit_status(
                unit
            )
            for unit in SERVICES
        ],
        "automatic_execution": False,
        "order_submission": False,
    }

    paths = deployment_paths()

    atomic_write_json(
        paths[
            "service_status"
        ],
        payload,
    )

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI Segment 4A-3 "
            "service controls"
        )
    )

    parser.add_argument(
        "command",
        choices=[
            "status",
            "daemon-start",
            "daemon-stop",
            "daemon-restart",
            "watchdog-start",
            "watchdog-stop",
            "health-start",
            "health-timer-enable",
            "health-timer-disable",
        ],
    )

    arguments = parser.parse_args()

    safety = safety_configuration()

    if not safety[
        "passed"
    ]:
        raise SystemExit(
            "Unsafe Segment 4A-3 configuration. "
            "Service action blocked."
        )

    mapping = {
        "daemon-start": (
            "start",
            "oanda-master-ai.service",
        ),
        "daemon-stop": (
            "stop",
            "oanda-master-ai.service",
        ),
        "daemon-restart": (
            "restart",
            "oanda-master-ai.service",
        ),
        "watchdog-start": (
            "start",
            "oanda-master-ai-watchdog.service",
        ),
        "watchdog-stop": (
            "stop",
            "oanda-master-ai-watchdog.service",
        ),
        "health-start": (
            "start",
            "oanda-master-ai-health.service",
        ),
        "health-timer-enable": (
            "enable",
            "--now",
            "oanda-master-ai-health.timer",
        ),
        "health-timer-disable": (
            "disable",
            "--now",
            "oanda-master-ai-health.timer",
        ),
    }

    if arguments.command == "status":
        print(
            json.dumps(
                status(),
                indent=2,
            )
        )

        return

    command = mapping[
        arguments.command
    ]

    completed = subprocess.run(
        [
            "sudo",
            "systemctl",
            *command,
        ],
        text=True,
        check=False,
    )

    raise SystemExit(
        completed.returncode
    )


if __name__ == "__main__":
    main()
