from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime
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


@dataclass
class BackupResult:
    timestamp: str
    successful: bool
    archive: str
    archive_size_bytes: int
    checksum_sha256: str
    included_paths: list[str]
    excluded_paths: list[str]
    removed_archives: list[str]
    free_disk_mb_before: float
    free_disk_mb_after: float
    error: str
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


class BackupManager:
    def __init__(
        self,
    ) -> None:
        root_config = (
            load_segment4a3_config()
        )

        self.config = root_config.get(
            "backups",
            {},
        )

        if not isinstance(
            self.config,
            dict,
        ):
            self.config = {}

        destination_value = str(
            self.config.get(
                "destination",
                "~/oanda_master_alliance_backups",
            )
        )

        self.destination = Path(
            destination_value
        ).expanduser()

        self.paths = deployment_paths()

    @staticmethod
    def _sha256(
        path: Path,
    ) -> str:
        digest = hashlib.sha256()

        with path.open(
            "rb"
        ) as handle:
            while True:
                chunk = handle.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                digest.update(
                    chunk
                )

        return digest.hexdigest()

    def _selected_paths(
        self,
    ) -> list[Path]:
        selected: list[
            Path
        ] = []

        mapping = {
            "include_state": (
                ROOT
                / "state"
            ),
            "include_reports": (
                ROOT
                / "reports"
            ),
            "include_logs": (
                ROOT
                / "logs"
            ),
            "include_config": (
                ROOT
                / "config"
            ),
        }

        for option, path in mapping.items():
            if bool(
                self.config.get(
                    option,
                    True,
                )
            ) and path.exists():
                selected.append(
                    path
                )

        if bool(
            self.config.get(
                "include_source",
                True,
            )
        ):
            source_paths = [
                "admin",
                "brain",
                "broker",
                "core",
                "execution",
                "expert_library",
                "market",
                "risk",
                "segment4",
                "signals",
                "trade_management",
                "deployment",
                "tests",
            ]

            for name in source_paths:
                path = (
                    ROOT
                    / name
                )

                if (
                    path.exists()
                    and path not in selected
                ):
                    selected.append(
                        path
                    )

            for pattern in (
                "*.py",
                "*.yaml",
                "*.yml",
                "requirements*.txt",
            ):
                for path in ROOT.glob(
                    pattern
                ):
                    if (
                        path.is_file()
                        and path not in selected
                    ):
                        selected.append(
                            path
                        )

        return selected

    def _excluded(
        self,
    ) -> list[str]:
        excluded = [
            ".venv",
            "__pycache__",
            ".git",
            "*.pyc",
            "*.pyo",
            "*.tmp",
        ]

        if bool(
            self.config.get(
                "exclude_credentials",
                True,
            )
        ):
            excluded.extend(
                [
                    ".env",
                    "*.key",
                    "*.pem",
                    "*token*",
                    "*credential*",
                ]
            )

        return excluded

    def _filter(
        self,
        tar_info: tarfile.TarInfo,
    ) -> tarfile.TarInfo | None:
        name = tar_info.name
        parts = Path(
            name
        ).parts

        blocked_exact = {
            ".venv",
            "__pycache__",
            ".git",
            ".env",
        }

        if any(
            part in blocked_exact
            for part in parts
        ):
            return None

        lowered = name.lower()

        if (
            lowered.endswith(
                ".pyc"
            )
            or lowered.endswith(
                ".pyo"
            )
            or lowered.endswith(
                ".pem"
            )
            or lowered.endswith(
                ".key"
            )
        ):
            return None

        if bool(
            self.config.get(
                "exclude_credentials",
                True,
            )
        ) and any(
            word in lowered
            for word in (
                "credential",
                "api_token",
                "secret_token",
            )
        ):
            return None

        return tar_info

    def _remove_old_archives(
        self,
    ) -> list[str]:
        retention = max(
            1,
            int(
                self.config.get(
                    "retention_count",
                    14,
                )
            ),
        )

        archives = sorted(
            self.destination.glob(
                "oanda_master_alliance_*.tar.gz"
            ),
            key=lambda path: (
                path.stat().st_mtime
            ),
            reverse=True,
        )

        removed: list[
            str
        ] = []

        for archive in archives[
            retention:
        ]:
            archive.unlink(
                missing_ok=True
            )

            checksum = Path(
                str(
                    archive
                )
                + ".sha256"
            )

            checksum.unlink(
                missing_ok=True
            )

            removed.append(
                str(
                    archive
                )
            )

        return removed

    def run(
        self,
    ) -> BackupResult:
        timestamp = utc_now_iso()
        free_before = free_disk_mb(
            ROOT
        )

        minimum_free = float(
            self.config.get(
                "minimum_free_disk_mb",
                2048,
            )
        )

        included = self._selected_paths()
        excluded = self._excluded()

        archive_path = Path()
        checksum_value = ""
        error = ""
        success = False
        archive_size = 0
        removed: list[
            str
        ] = []

        try:
            if not bool(
                self.config.get(
                    "enabled",
                    True,
                )
            ):
                raise RuntimeError(
                    "Backups are disabled."
                )

            if free_before < minimum_free:
                raise RuntimeError(
                    (
                        "Insufficient free disk space: "
                        f"{free_before:.2f} MB"
                    )
                )

            if not included:
                raise RuntimeError(
                    "No backup paths were selected."
                )

            self.destination.mkdir(
                parents=True,
                exist_ok=True,
            )

            stamp = datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )

            archive_path = (
                self.destination
                / (
                    "oanda_master_alliance_"
                    f"{stamp}.tar.gz"
                )
            )

            temporary = archive_path.with_suffix(
                archive_path.suffix
                + ".tmp"
            )

            with tarfile.open(
                temporary,
                "w:gz",
            ) as archive:
                for path in included:
                    archive.add(
                        path,
                        arcname=str(
                            path.relative_to(
                                ROOT
                            )
                        ),
                        recursive=True,
                        filter=self._filter,
                    )

            temporary.replace(
                archive_path
            )

            archive_size = (
                archive_path.stat().st_size
            )

            checksum_value = self._sha256(
                archive_path
            )

            checksum_path = Path(
                str(
                    archive_path
                )
                + ".sha256"
            )

            checksum_path.write_text(
                (
                    f"{checksum_value}  "
                    f"{archive_path.name}\n"
                ),
                encoding="utf-8",
            )

            with tarfile.open(
                archive_path,
                "r:gz",
            ) as archive:
                archive.getmembers()

            removed = (
                self._remove_old_archives()
            )

            success = True

        except Exception as exc:
            error = (
                f"{type(exc).__name__}: {exc}"
            )

            if (
                archive_path
                and archive_path.exists()
                and not success
            ):
                archive_path.unlink(
                    missing_ok=True
                )

        result = BackupResult(
            timestamp=timestamp,
            successful=success,
            archive=(
                str(
                    archive_path
                )
                if archive_path
                else ""
            ),
            archive_size_bytes=archive_size,
            checksum_sha256=checksum_value,
            included_paths=[
                str(
                    path.relative_to(
                        ROOT
                    )
                )
                for path in included
            ],
            excluded_paths=excluded,
            removed_archives=removed,
            free_disk_mb_before=round(
                free_before,
                2,
            ),
            free_disk_mb_after=round(
                free_disk_mb(
                    ROOT
                ),
                2,
            ),
            error=error,
        )

        atomic_write_json(
            self.paths[
                "backup_status"
            ],
            result.to_dict(),
        )

        append_jsonl(
            self.paths[
                "backup_history"
            ],
            result.to_dict(),
        )

        return result
