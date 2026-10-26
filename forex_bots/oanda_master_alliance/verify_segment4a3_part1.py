from __future__ import annotations

import json
import os
import sys
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


from segment4.deployment_common import (
    load_segment4a3_config,
    safety_configuration,
)


def main() -> None:
    config = load_segment4a3_config()
    safety = safety_configuration()

    required_files = [
        "config/segment4a3.yaml",
        "segment4/deployment_common.py",
        "deployment/scripts/run_master_ai.sh",
        "deployment/scripts/run_watchdog.sh",
        "deployment/scripts/run_health.sh",
        "deployment/scripts/run_recovery.sh",
        (
            "deployment/scripts/"
            "install_systemd_units.sh"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-watchdog.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-health.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-health.timer"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-recovery.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai.target"
        ),
        "admin/segment4_services.py",
    ]

    missing = [
        name
        for name in required_files
        if not (
            ROOT
            / name
        ).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4A-3 Part 1 files: "
            + ", ".join(
                missing
            )
        )

    executable_files = [
        (
            ROOT
            / "deployment/scripts/"
            "run_master_ai.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_watchdog.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_health.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_recovery.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "install_systemd_units.sh"
        ),
    ]

    for path in executable_files:
        if not os.access(
            path,
            os.X_OK,
        ):
            raise AssertionError(
                f"Script is not executable: {path}"
            )

    assert (
        config[
            "practice_only"
        ]
        is True
    )

    assert (
        config[
            "live_environment_allowed"
        ]
        is False
    )

    assert (
        config[
            "automatic_execution"
        ]
        is False
    )

    assert (
        config[
            (
                "practice_order_"
                "submission_enabled"
            )
        ]
        is False
    )

    assert safety[
        "passed"
    ]

    payload = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "practice_only": (
            config[
                "practice_only"
            ]
        ),
        "live_environment_allowed": (
            config[
                "live_environment_allowed"
            ]
        ),
        "automatic_execution": (
            config[
                "automatic_execution"
            ]
        ),
        "practice_order_submission_enabled": (
            config[
                (
                    "practice_order_"
                    "submission_enabled"
                )
            ]
        ),
        "system_services_auto_enabled": (
            config[
                "systemd"
            ][
                (
                    "enable_main_service_"
                    "on_boot"
                )
            ]
        ),
        "watchdog_auto_enabled": (
            config[
                "systemd"
            ][
                (
                    "enable_watchdog_"
                    "on_boot"
                )
            ]
        ),
    }

    print()
    print(
        "OANDA MASTER AI — SEGMENT 4A-3 PART 1"
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
        "Segment 4A-3 Part 1 verification passed."
    )


if __name__ == "__main__":
    main()
