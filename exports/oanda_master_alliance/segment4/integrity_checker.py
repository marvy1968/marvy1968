from __future__ import annotations

import compileall
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from core.common import ROOT
from segment4.deployment_common import (
    safety_configuration,
    utc_now_iso,
)


@dataclass
class IntegrityCheck:
    name: str
    passed: bool
    message: str
    metadata: dict[str, Any]

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


class IntegrityChecker:
    def __init__(
        self,
    ) -> None:
        self.required_files = [
            "run_segment4.py",
            "run_segment4_watchdog.py",
            "run_segment4_health.py",
            "run_segment4_recovery.py",
            "run_segment4_backup.py",
            "segment4/daemon.py",
            "segment4/watchdog.py",
            "segment4/recovery.py",
            "segment4/health_monitor.py",
            "segment4/backup_manager.py",
            "segment4/integrity_checker.py",
            "config/master_ai.yaml",
            "config/segment2.yaml",
            "config/segment3.yaml",
            "config/segment4.yaml",
            "config/segment4a3.yaml",
        ]

    @staticmethod
    def _file_hash(
        path: Path,
    ) -> str:
        digest = hashlib.sha256()

        with path.open(
            "rb"
        ) as handle:
            while True:
                block = handle.read(
                    1024 * 1024
                )

                if not block:
                    break

                digest.update(
                    block
                )

        return digest.hexdigest()

    def _required_files(
        self,
    ) -> IntegrityCheck:
        missing = [
            name
            for name in self.required_files
            if not (
                ROOT
                / name
            ).exists()
        ]

        return IntegrityCheck(
            name="required_files",
            passed=not missing,
            message=(
                "All required files are present."
                if not missing
                else "Required files are missing."
            ),
            metadata={
                "required_count": len(
                    self.required_files
                ),
                "missing": missing,
            },
        )

    def _yaml_files(
        self,
    ) -> IntegrityCheck:
        errors: dict[
            str,
            str
        ] = {}

        for path in (
            ROOT
            / "config"
        ).glob(
            "*.yaml"
        ):
            try:
                yaml.safe_load(
                    path.read_text(
                        encoding="utf-8"
                    )
                )

            except Exception as exc:
                errors[
                    str(
                        path.relative_to(
                            ROOT
                        )
                    )
                ] = str(
                    exc
                )

        return IntegrityCheck(
            name="yaml_configuration",
            passed=not errors,
            message=(
                "YAML configuration is valid."
                if not errors
                else "YAML configuration errors detected."
            ),
            metadata={
                "errors": errors,
            },
        )

    def _python_compile(
        self,
    ) -> IntegrityCheck:
        passed = compileall.compile_dir(
            str(
                ROOT
            ),
            quiet=1,
            force=False,
            rx=None,
        )

        return IntegrityCheck(
            name="python_compilation",
            passed=passed,
            message=(
                "Python compilation passed."
                if passed
                else "Python compilation failed."
            ),
            metadata={},
        )

    def _safety(
        self,
    ) -> IntegrityCheck:
        safety = safety_configuration()

        return IntegrityCheck(
            name="safety_configuration",
            passed=bool(
                safety.get(
                    "passed"
                )
            ),
            message=(
                "Safety configuration passed."
                if safety.get(
                    "passed"
                )
                else "Unsafe configuration detected."
            ),
            metadata=safety,
        )

    def _manifest(
        self,
    ) -> IntegrityCheck:
        hashes: dict[
            str,
            str
        ] = {}

        for name in self.required_files:
            path = (
                ROOT
                / name
            )

            if path.exists() and path.is_file():
                hashes[
                    name
                ] = self._file_hash(
                    path
                )

        manifest_path = (
            ROOT
            / "state/segment4a3/"
            "integrity_manifest.json"
        )

        manifest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        manifest_path.write_text(
            json.dumps(
                {
                    "timestamp": utc_now_iso(),
                    "files": hashes,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        return IntegrityCheck(
            name="integrity_manifest",
            passed=True,
            message=(
                "Integrity manifest generated."
            ),
            metadata={
                "path": str(
                    manifest_path
                ),
                "file_count": len(
                    hashes
                ),
            },
        )

    def run(
        self,
    ) -> dict[str, Any]:
        checks = [
            self._required_files(),
            self._yaml_files(),
            self._python_compile(),
            self._safety(),
            self._manifest(),
        ]

        passed = all(
            check.passed
            for check in checks
        )

        return {
            "timestamp": utc_now_iso(),
            "passed": passed,
            "checks": [
                check.to_dict()
                for check in checks
            ],
            "automatic_execution": False,
            "order_submission": False,
        }
