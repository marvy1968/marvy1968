from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.common import ROOT
from segment4.deployment_common import (
    append_jsonl,
    atomic_write_json,
    deployment_paths,
    free_disk_mb,
    load_segment4a3_config,
    utc_now_iso,
)
from segment4.integrity_checker import (
    IntegrityChecker,
)


@dataclass
class MaintenanceAction:
    name: str
    successful: bool
    affected_files: list[str]
    message: str

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


class MaintenanceManager:
    def __init__(
        self,
    ) -> None:
        root_config = (
            load_segment4a3_config()
        )

        self.config = root_config.get(
            "maintenance",
            {},
        )

        if not isinstance(
            self.config,
            dict,
        ):
            self.config = {}

        self.backup_config = (
            root_config.get(
                "backups",
                {},
            )
        )

        if not isinstance(
            self.backup_config,
            dict,
        ):
            self.backup_config = {}

        self.paths = deployment_paths()

    @staticmethod
    def _older_than(
        path: Path,
        days: int,
    ) -> bool:
        threshold = (
            datetime.now().timestamp()
            - timedelta(
                days=max(
                    0,
                    days,
                )
            ).total_seconds()
        )

        return (
            path.stat().st_mtime
            < threshold
        )

    def _remove_old_files(
        self,
        *,
        root: Path,
        patterns: tuple[str, ...],
        age_days: int,
        action_name: str,
    ) -> MaintenanceAction:
        removed: list[
            str
        ] = []

        try:
            if root.exists():
                for pattern in patterns:
                    for path in root.rglob(
                        pattern
                    ):
                        if (
                            path.is_file()
                            and self._older_than(
                                path,
                                age_days,
                            )
                        ):
                            path.unlink(
                                missing_ok=True
                            )

                            removed.append(
                                str(
                                    path.relative_to(
                                        ROOT
                                    )
                                )
                                if path.is_relative_to(
                                    ROOT
                                )
                                else str(
                                    path
                                )
                            )

            return MaintenanceAction(
                name=action_name,
                successful=True,
                affected_files=removed,
                message=(
                    f"Removed {len(removed)} old files."
                ),
            )

        except Exception as exc:
            return MaintenanceAction(
                name=action_name,
                successful=False,
                affected_files=removed,
                message=(
                    f"{type(exc).__name__}: {exc}"
                ),
            )

    def _compress_logs(
        self,
    ) -> MaintenanceAction:
        compressed: list[
            str
        ] = []

        age_days = int(
            self.config.get(
                "compress_logs_after_days",
                2,
            )
        )

        try:
            logs = (
                ROOT
                / "logs"
            )

            if logs.exists():
                for path in logs.glob(
                    "*.log"
                ):
                    if not self._older_than(
                        path,
                        age_days,
                    ):
                        continue

                    destination = path.with_suffix(
                        path.suffix
                        + ".gz"
                    )

                    if destination.exists():
                        continue

                    with path.open(
                        "rb"
                    ) as source:
                        with gzip.open(
                            destination,
                            "wb",
                        ) as target:
                            shutil.copyfileobj(
                                source,
                                target,
                            )

                    path.unlink(
                        missing_ok=True
                    )

                    compressed.append(
                        str(
                            destination.relative_to(
                                ROOT
                            )
                        )
                    )

            return MaintenanceAction(
                name="compress_old_logs",
                successful=True,
                affected_files=compressed,
                message=(
                    f"Compressed {len(compressed)} logs."
                ),
            )

        except Exception as exc:
            return MaintenanceAction(
                name="compress_old_logs",
                successful=False,
                affected_files=compressed,
                message=(
                    f"{type(exc).__name__}: {exc}"
                ),
            )

    def _truncate_oversized_logs(
        self,
    ) -> MaintenanceAction:
        maximum_mb = float(
            self.config.get(
                "maximum_single_log_mb",
                100,
            )
        )

        maximum_bytes = int(
            maximum_mb
            * 1024
            * 1024
        )

        affected: list[
            str
        ] = []

        try:
            logs = (
                ROOT
                / "logs"
            )

            if logs.exists():
                for path in logs.iterdir():
                    if (
                        not path.is_file()
                        or path.suffix
                        not in {
                            ".log",
                            ".jsonl",
                        }
                    ):
                        continue

                    if (
                        path.stat().st_size
                        <= maximum_bytes
                    ):
                        continue

                    rotated = path.with_name(
                        path.name
                        + ".maintenance-rotated"
                    )

                    path.replace(
                        rotated
                    )

                    path.touch()

                    affected.append(
                        str(
                            rotated.relative_to(
                                ROOT
                            )
                        )
                    )

            return MaintenanceAction(
                name="oversized_log_rotation",
                successful=True,
                affected_files=affected,
                message=(
                    f"Rotated {len(affected)} oversized logs."
                ),
            )

        except Exception as exc:
            return MaintenanceAction(
                name="oversized_log_rotation",
                successful=False,
                affected_files=affected,
                message=(
                    f"{type(exc).__name__}: {exc}"
                ),
            )

    def run(
        self,
    ) -> dict[str, Any]:
        timestamp = utc_now_iso()
        actions: list[
            MaintenanceAction
        ] = []

        if not bool(
            self.config.get(
                "enabled",
                True,
            )
        ):
            result = {
                "timestamp": timestamp,
                "successful": False,
                "error": (
                    "Maintenance is disabled."
                ),
                "actions": [],
                "automatic_execution": False,
                "order_submission": False,
            }

            atomic_write_json(
                self.paths[
                    "maintenance_status"
                ],
                result,
            )

            append_jsonl(
                self.paths[
                    "maintenance_history"
                ],
                result,
            )

            return result

        actions.append(
            self._remove_old_files(
                root=(
                    ROOT
                    / "logs"
                ),
                patterns=(
                    "*.jsonl",
                ),
                age_days=int(
                    self.config.get(
                        "maximum_jsonl_age_days",
                        30,
                    )
                ),
                action_name=(
                    "old_jsonl_cleanup"
                ),
            )
        )

        actions.append(
            self._remove_old_files(
                root=(
                    ROOT
                    / "logs"
                ),
                patterns=(
                    "*.log.gz",
                    "*.maintenance-rotated",
                ),
                age_days=int(
                    self.config.get(
                        (
                            "maximum_console_"
                            "log_age_days"
                        ),
                        14,
                    )
                ),
                action_name=(
                    "old_console_log_cleanup"
                ),
            )
        )

        actions.append(
            self._remove_old_files(
                root=(
                    ROOT
                    / "reports"
                ),
                patterns=(
                    "*.md",
                    "*.json",
                    "*.csv",
                ),
                age_days=int(
                    self.config.get(
                        "maximum_report_age_days",
                        90,
                    )
                ),
                action_name=(
                    "old_report_cleanup"
                ),
            )
        )

        backup_destination = Path(
            str(
                self.backup_config.get(
                    "destination",
                    "~/oanda_master_alliance_backups",
                )
            )
        ).expanduser()

        actions.append(
            self._remove_old_files(
                root=backup_destination,
                patterns=(
                    "*.tar.gz",
                    "*.sha256",
                ),
                age_days=int(
                    self.config.get(
                        "maximum_backup_age_days",
                        30,
                    )
                ),
                action_name=(
                    "old_backup_cleanup"
                ),
            )
        )

        actions.append(
            self._compress_logs()
        )

        actions.append(
            self._truncate_oversized_logs()
        )

        integrity: dict[
            str,
            Any
        ] = {
            "passed": True,
            "checks": [],
        }

        if bool(
            self.config.get(
                "verify_python_compilation",
                True,
            )
        ) or bool(
            self.config.get(
                "verify_configuration_safety",
                True,
            )
        ):
            integrity = (
                IntegrityChecker().run()
            )

        successful = (
            all(
                action.successful
                for action in actions
            )
            and bool(
                integrity.get(
                    "passed",
                    False,
                )
            )
        )

        result = {
            "timestamp": timestamp,
            "successful": successful,
            "actions": [
                action.to_dict()
                for action in actions
            ],
            "integrity": integrity,
            "free_disk_mb": round(
                free_disk_mb(
                    ROOT
                ),
                2,
            ),
            "automatic_execution": False,
            "order_submission": False,
        }

        atomic_write_json(
            self.paths[
                "maintenance_status"
            ],
            result,
        )

        append_jsonl(
            self.paths[
                "maintenance_history"
            ],
            result,
        )

        return result
