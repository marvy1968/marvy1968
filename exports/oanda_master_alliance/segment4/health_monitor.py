from __future__ import annotations

import json
import shutil
from dataclasses import (
    asdict,
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

from segment4.common import (
    ROOT,
    age_seconds,
    append_jsonl,
    atomic_write_json,
    process_exists,
    read_json,
    utc_now_iso,
)
from segment4.operations_common import (
    file_age_seconds,
    load_operations_config,
    operations_paths,
    read_pid_file,
)


@dataclass
class HealthCheck:
    name: str
    passed: bool
    severity: str
    message: str
    value: Any = None
    threshold: Any = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class HealthReport:
    timestamp: str
    healthy: bool
    critical_failures: int
    warnings: int
    checks: list[HealthCheck]
    automatic_restart_enabled: bool
    automatic_execution: bool
    order_submission: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "healthy": self.healthy,
            "critical_failures": (
                self.critical_failures
            ),
            "warnings": self.warnings,
            "checks": [
                check.to_dict()
                for check in self.checks
            ],
            "automatic_restart_enabled": (
                self.automatic_restart_enabled
            ),
            "automatic_execution": (
                self.automatic_execution
            ),
            "order_submission": (
                self.order_submission
            ),
        }


class HealthMonitor:
    def __init__(
        self,
    ) -> None:
        self.config = (
            load_operations_config()
        )

        self.paths = operations_paths()

        self.segment4_status_path = (
            ROOT
            / "state/segment4/status.json"
        )

        self.segment4_heartbeat_path = (
            ROOT
            / "state/segment4/heartbeat.json"
        )

        self.segment4_pid_path = (
            ROOT
            / "state/segment4/"
            "segment4_daemon.pid"
        )

        self.segment2_shortlist_path = (
            ROOT
            / "state/decisions/"
            "latest_shortlist.json"
        )

        self.segment3_status_path = (
            ROOT
            / "state/execution/"
            "segment3_status.json"
        )

        self.market_snapshot_path = (
            ROOT
            / "state/market_snapshot.json"
        )

    def _check_process(
        self,
    ) -> HealthCheck:
        pid = read_pid_file(
            self.segment4_pid_path
        )

        if not pid:
            return HealthCheck(
                name="segment4_process",
                passed=True,
                severity="info",
                message=(
                    "Segment 4 daemon is not running."
                ),
                value=None,
                metadata={
                    "expected_for_once_mode": True,
                },
            )

        running = process_exists(
            pid
        )

        return HealthCheck(
            name="segment4_process",
            passed=running,
            severity=(
                "critical"
                if not running
                else "info"
            ),
            message=(
                "Segment 4 process is running."
                if running
                else (
                    "Segment 4 PID exists but "
                    "the process is not running."
                )
            ),
            value=pid,
        )

    def _check_heartbeat(
        self,
    ) -> HealthCheck:
        heartbeat = read_json(
            self.segment4_heartbeat_path,
            default={},
        )

        if not isinstance(
            heartbeat,
            dict,
        ):
            heartbeat = {}

        timestamp = heartbeat.get(
            "timestamp"
        )

        heartbeat_age = age_seconds(
            str(
                timestamp
                or ""
            )
        )

        maximum = float(
            self.config.get(
                (
                    "maximum_heartbeat_"
                    "age_seconds"
                ),
                180,
            )
        )

        daemon_running = bool(
            read_json(
                self.segment4_status_path,
                default={},
            ).get(
                "daemon_running",
                False,
            )
        )

        if not daemon_running:
            return HealthCheck(
                name="heartbeat_freshness",
                passed=True,
                severity="info",
                message=(
                    "Daemon is stopped; stale "
                    "heartbeat is not treated "
                    "as a failure."
                ),
                value=heartbeat_age,
                threshold=maximum,
            )

        passed = (
            heartbeat_age is not None
            and heartbeat_age
            <= maximum
        )

        return HealthCheck(
            name="heartbeat_freshness",
            passed=passed,
            severity=(
                "critical"
                if not passed
                else "info"
            ),
            message=(
                "Heartbeat is fresh."
                if passed
                else "Heartbeat is stale."
            ),
            value=heartbeat_age,
            threshold=maximum,
        )

    def _check_status_freshness(
        self,
    ) -> HealthCheck:
        age = file_age_seconds(
            self.segment4_status_path
        )

        maximum = float(
            self.config.get(
                "maximum_status_age_seconds",
                300,
            )
        )

        status = read_json(
            self.segment4_status_path,
            default={},
        )

        daemon_running = bool(
            status.get(
                "daemon_running",
                False,
            )
        )

        if not daemon_running:
            return HealthCheck(
                name="status_freshness",
                passed=True,
                severity="info",
                message=(
                    "Daemon is stopped; status "
                    "age is informational."
                ),
                value=age,
                threshold=maximum,
            )

        passed = (
            age is not None
            and age <= maximum
        )

        return HealthCheck(
            name="status_freshness",
            passed=passed,
            severity=(
                "critical"
                if not passed
                else "info"
            ),
            message=(
                "Runtime status is fresh."
                if passed
                else "Runtime status is stale."
            ),
            value=age,
            threshold=maximum,
        )

    def _check_market_snapshot(
        self,
    ) -> HealthCheck:
        required = bool(
            self.config.get(
                (
                    "require_foundation_"
                    "snapshot"
                ),
                True,
            )
        )

        exists = (
            self.market_snapshot_path.exists()
        )

        payload = read_json(
            self.market_snapshot_path,
            default={},
        )

        data_quality = bool(
            payload.get(
                "data_quality_passed",
                False,
            )
        )

        passed = (
            exists
            and data_quality
        )

        if not required:
            passed = True

        return HealthCheck(
            name="foundation_snapshot",
            passed=passed,
            severity=(
                "critical"
                if required
                and not passed
                else "info"
            ),
            message=(
                "Foundation snapshot is present "
                "and data quality passed."
                if exists
                and data_quality
                else (
                    "Foundation snapshot is missing "
                    "or data quality failed."
                )
            ),
            value={
                "exists": exists,
                "data_quality_passed": (
                    data_quality
                ),
            },
        )

    def _check_segment2(
        self,
    ) -> HealthCheck:
        required = bool(
            self.config.get(
                (
                    "require_segment2_"
                    "shortlist"
                ),
                False,
            )
        )

        exists = (
            self.segment2_shortlist_path
            .exists()
        )

        age = file_age_seconds(
            self.segment2_shortlist_path
        )

        maximum_minutes = float(
            self.config.get(
                (
                    "maximum_segment2_"
                    "age_minutes"
                ),
                30,
            )
        )

        maximum_seconds = (
            maximum_minutes
            * 60.0
        )

        fresh = (
            age is not None
            and age
            <= maximum_seconds
        )

        passed = (
            exists
            and fresh
        )

        if not required:
            passed = True

        return HealthCheck(
            name="segment2_shortlist",
            passed=passed,
            severity=(
                "warning"
                if required
                and not passed
                else "info"
            ),
            message=(
                "Segment 2 shortlist is fresh."
                if exists
                and fresh
                else (
                    "Segment 2 shortlist is missing "
                    "or stale."
                )
            ),
            value=age,
            threshold=maximum_seconds,
            metadata={
                "required": required,
                "exists": exists,
            },
        )

    def _check_segment3(
        self,
    ) -> HealthCheck:
        required = bool(
            self.config.get(
                "require_segment3_status",
                True,
            )
        )

        exists = (
            self.segment3_status_path.exists()
        )

        age = file_age_seconds(
            self.segment3_status_path
        )

        maximum_minutes = float(
            self.config.get(
                (
                    "maximum_segment3_"
                    "age_minutes"
                ),
                30,
            )
        )

        maximum_seconds = (
            maximum_minutes
            * 60.0
        )

        fresh = (
            age is not None
            and age
            <= maximum_seconds
        )

        payload = read_json(
            self.segment3_status_path,
            default={},
        )

        practice_only = bool(
            payload.get(
                "practice_only",
                True,
            )
        )

        automatic_execution = bool(
            payload.get(
                "automatic_execution",
                False,
            )
        )

        submission_enabled = bool(
            payload.get(
                "practice_order_submission_enabled",
                False,
            )
        )

        safe = (
            practice_only
            and not automatic_execution
            and not submission_enabled
        )

        passed = (
            exists
            and fresh
            and safe
        )

        if not required:
            passed = safe

        return HealthCheck(
            name="segment3_status",
            passed=passed,
            severity=(
                "critical"
                if not safe
                else (
                    "warning"
                    if required
                    and not passed
                    else "info"
                )
            ),
            message=(
                "Segment 3 status is fresh "
                "and safety settings passed."
                if passed
                else (
                    "Segment 3 status is missing, "
                    "stale or unsafe."
                )
            ),
            value={
                "age_seconds": age,
                "practice_only": practice_only,
                "automatic_execution": (
                    automatic_execution
                ),
                "submission_enabled": (
                    submission_enabled
                ),
            },
            threshold=maximum_seconds,
        )

    def _check_disk(
        self,
    ) -> HealthCheck:
        usage = shutil.disk_usage(
            ROOT
        )

        free_mb = (
            usage.free
            / 1024
            / 1024
        )

        minimum = float(
            self.config.get(
                "minimum_free_disk_mb",
                1024,
            )
        )

        passed = (
            free_mb
            >= minimum
        )

        return HealthCheck(
            name="free_disk_space",
            passed=passed,
            severity=(
                "critical"
                if not passed
                else "info"
            ),
            message=(
                "Free disk space is sufficient."
                if passed
                else "Free disk space is low."
            ),
            value=round(
                free_mb,
                2,
            ),
            threshold=minimum,
        )

    def _check_log_sizes(
        self,
    ) -> HealthCheck:
        maximum_mb = float(
            self.config.get(
                "maximum_log_file_mb",
                100,
            )
        )

        oversized: list[
            dict[str, Any]
        ] = []

        logs_directory = (
            ROOT
            / "logs"
        )

        if logs_directory.exists():
            for path in logs_directory.glob(
                "*.jsonl"
            ):
                try:
                    size_mb = (
                        path.stat().st_size
                        / 1024
                        / 1024
                    )

                except OSError:
                    continue

                if size_mb > maximum_mb:
                    oversized.append(
                        {
                            "file": str(
                                path.relative_to(
                                    ROOT
                                )
                            ),
                            "size_mb": round(
                                size_mb,
                                2,
                            ),
                        }
                    )

        passed = (
            len(
                oversized
            )
            == 0
        )

        return HealthCheck(
            name="log_file_sizes",
            passed=passed,
            severity=(
                "warning"
                if not passed
                else "info"
            ),
            message=(
                "Log files are within limits."
                if passed
                else (
                    "One or more log files "
                    "exceed the configured size."
                )
            ),
            value=oversized,
            threshold=maximum_mb,
        )

    def run(
        self,
    ) -> HealthReport:
        checks = [
            self._check_process(),
            self._check_heartbeat(),
            self._check_status_freshness(),
            self._check_market_snapshot(),
            self._check_segment2(),
            self._check_segment3(),
            self._check_disk(),
            self._check_log_sizes(),
        ]

        critical_failures = sum(
            1
            for check in checks
            if not check.passed
            and check.severity
            == "critical"
        )

        warnings = sum(
            1
            for check in checks
            if not check.passed
            and check.severity
            == "warning"
        )

        report = HealthReport(
            timestamp=utc_now_iso(),
            healthy=(
                critical_failures
                == 0
            ),
            critical_failures=(
                critical_failures
            ),
            warnings=warnings,
            checks=checks,
            automatic_restart_enabled=bool(
                self.config.get(
                    (
                        "automatic_restart_"
                        "enabled"
                    ),
                    False,
                )
            ),
            automatic_execution=False,
            order_submission=False,
        )

        atomic_write_json(
            self.paths[
                "health_status"
            ],
            report.to_dict(),
        )

        append_jsonl(
            self.paths[
                "health_history"
            ],
            report.to_dict(),
        )

        return report
