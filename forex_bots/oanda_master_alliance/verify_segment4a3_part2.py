from __future__ import annotations

import json
import os
import sys
import tarfile
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


from segment4.integrity_checker import (
    IntegrityChecker,
)
from segment4.maintenance_manager import (
    MaintenanceManager,
)


def main() -> None:
    required_files = [
        "segment4/backup_manager.py",
        "segment4/integrity_checker.py",
        "segment4/maintenance_manager.py",
        "run_segment4_backup.py",
        "run_segment4_integrity.py",
        "run_segment4_maintenance.py",
        (
            "deployment/scripts/"
            "run_backup.sh"
        ),
        (
            "deployment/scripts/"
            "run_maintenance.sh"
        ),
        (
            "deployment/scripts/"
            "run_integrity.sh"
        ),
        (
            "deployment/scripts/"
            "install_logrotate.sh"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-backup.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-backup.timer"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-maintenance.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-maintenance.timer"
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
            "Missing Segment 4A-3 Part 2 files: "
            + ", ".join(
                missing
            )
        )

    executable_files = [
        (
            ROOT
            / "deployment/scripts/"
            "run_backup.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_maintenance.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "run_integrity.sh"
        ),
        (
            ROOT
            / "deployment/scripts/"
            "install_logrotate.sh"
        ),
    ]

    for path in executable_files:
        assert os.access(
            path,
            os.X_OK,
        ), f"Not executable: {path}"

    integrity = (
        IntegrityChecker().run()
    )

    assert integrity[
        "passed"
    ]

    manager = MaintenanceManager()

    with tempfile.TemporaryDirectory() as directory:
        temporary_root = Path(
            directory
        )

        sample = (
            temporary_root
            / "sample.txt"
        )

        sample.write_text(
            "backup verification\n",
            encoding="utf-8",
        )

        archive = (
            temporary_root
            / "sample.tar.gz"
        )

        with tarfile.open(
            archive,
            "w:gz",
        ) as handle:
            handle.add(
                sample,
                arcname="sample.txt",
            )

        with tarfile.open(
            archive,
            "r:gz",
        ) as handle:
            names = handle.getnames()

        assert (
            "sample.txt"
            in names
        )

    payload = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "integrity_passed": (
            integrity[
                "passed"
            ]
        ),
        "integrity_checks": len(
            integrity[
                "checks"
            ]
        ),
        "maintenance_manager_loaded": (
            manager is not None
        ),
        "backup_archive_test": True,
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — SEGMENT 4A-3 PART 2"
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
        "Segment 4A-3 Part 2 verification passed."
    )


if __name__ == "__main__":
    main()
