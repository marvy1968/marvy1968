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


from segment4.alert_manager import (
    AlertManager,
)
from segment4.deployment_common import (
    load_segment4a3_config,
    safety_configuration,
)
from segment4.integrity_checker import (
    IntegrityChecker,
)


def main() -> None:
    config = load_segment4a3_config()
    safety = safety_configuration()

    required_files = [
        "segment4/alert_manager.py",
        "segment4/backup_manager.py",
        "segment4/maintenance_manager.py",
        "segment4/integrity_checker.py",
        "run_segment4_alerts.py",
        "run_segment4_backup.py",
        "run_segment4_maintenance.py",
        "run_segment4_integrity.py",
        "build_segment4a3_report.py",
        "admin/segment4_services.py",
        (
            "deployment/scripts/"
            "install_systemd_units.sh"
        ),
        (
            "deployment/scripts/"
            "install_logrotate.sh"
        ),
        (
            "deployment/scripts/"
            "run_report.sh"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-report.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-report.timer"
        ),
        (
            "deployment/logrotate/"
            "oanda-master-ai"
        ),
    ]

    missing = [
        file_name
        for file_name in required_files
        if not (
            ROOT
            / file_name
        ).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4A-3 files: "
            + ", ".join(
                missing
            )
        )

    executable_files = [
        (
            ROOT
            / "deployment/scripts/"
            "install_systemd_units.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "install_logrotate.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_report.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_alerts.sh"
        ),
    ]

    for path in executable_files:
        assert os.access(
            path,
            os.X_OK,
        ), f"Not executable: {path}"

    assert safety[
        "passed"
    ]

    integrity = IntegrityChecker().run()

    assert integrity[
        "passed"
    ]

    assert config[
        "practice_only"
    ] is True

    assert config[
        "live_environment_allowed"
    ] is False

    assert config[
        "automatic_execution"
    ] is False

    assert config[
        (
            "practice_order_"
            "submission_enabled"
        )
    ] is False

    alert_manager = AlertManager()

    assert alert_manager is not None

    result = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "integrity_passed": (
            integrity[
                "passed"
            ]
        ),
        "safety_passed": (
            safety[
                "passed"
            ]
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
        "order_submission": (
            config[
                (
                    "practice_order_"
                    "submission_enabled"
                )
            ]
        ),
        "main_service_auto_enabled": (
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
        "OANDA MASTER AI — SEGMENT 4A-3"
    )
    print("=" * 72)

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4A-3 verification passed."
    )


if __name__ == "__main__":
    main()
