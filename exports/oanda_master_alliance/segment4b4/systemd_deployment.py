from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from segment4b4.common import (
    ROOT,
    atomic_write_json,
    read_json,
    utc_now_iso,
)


UNIT_NAMES = [
    "oanda-master-ai-shadow-campaign.service",
    "oanda-master-ai-shadow-campaign-health.service",
    "oanda-master-ai-shadow-campaign-health.timer",
    "oanda-master-ai-shadow-campaign-snapshot.service",
    "oanda-master-ai-shadow-campaign-snapshot.timer",
    "oanda-master-ai-shadow-campaign-alert.service",
    "oanda-master-ai-shadow-campaign-alert.timer",
]


MONITORING_TIMERS = [
    "oanda-master-ai-shadow-campaign-health.timer",
    "oanda-master-ai-shadow-campaign-snapshot.timer",
    "oanda-master-ai-shadow-campaign-alert.timer",
]


CAMPAIGN_SERVICE = (
    "oanda-master-ai-shadow-campaign.service"
)


@dataclass(slots=True)
class UnitInspection:
    unit: str
    source_exists: bool
    installed: bool
    active: str
    enabled: str
    load_state: str
    unit_file_state: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DeploymentInspection:
    timestamp: str
    systemctl_available: bool
    systemd_running: bool
    all_sources_present: bool
    all_units_installed: bool
    monitoring_timers_enabled: bool
    campaign_service_active: bool
    campaign_service_enabled: bool
    units: list[UnitInspection]
    automatic_promotion: bool = False
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["units"] = [
            item.to_dict()
            for item in self.units
        ]
        return payload


class SystemdDeploymentManager:
    def __init__(
        self,
        *,
        source_directory: Path | None = None,
        target_directory: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.source_directory = (
            source_directory
            if source_directory is not None
            else ROOT / "deployment/systemd"
        )

        self.target_directory = (
            target_directory
            if target_directory is not None
            else Path("/etc/systemd/system")
        )

        self.state_path = (
            state_path
            if state_path is not None
            else (
                ROOT
                / "state/shadow_campaign"
                / "systemd_deployment.json"
            )
        )

    @staticmethod
    def _run(
        *arguments: str,
    ) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                list(arguments),
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (
            OSError,
            subprocess.TimeoutExpired,
        ) as exc:
            return (
                1,
                "",
                f"{type(exc).__name__}: {exc}",
            )

        return (
            completed.returncode,
            completed.stdout.strip(),
            completed.stderr.strip(),
        )

    @staticmethod
    def _clean_value(
        stdout: str,
        stderr: str,
        default: str,
    ) -> str:
        value = stdout.strip() or stderr.strip()
        return value or default

    def systemctl_available(self) -> bool:
        return shutil.which("systemctl") is not None

    def systemd_running(self) -> bool:
        if not self.systemctl_available():
            return False

        code, stdout, _ = self._run(
            "systemctl",
            "is-system-running",
        )

        state = stdout.strip().lower()

        return (
            code == 0
            or state
            in {
                "running",
                "degraded",
                "starting",
            }
        )

    def inspect_unit(
        self,
        unit: str,
    ) -> UnitInspection:
        source_path = self.source_directory / unit
        installed_path = self.target_directory / unit

        source_exists = source_path.is_file()

        installed = (
            installed_path.is_file()
            or installed_path.is_symlink()
        )

        if not self.systemctl_available():
            return UnitInspection(
                unit=unit,
                source_exists=source_exists,
                installed=installed,
                active="unavailable",
                enabled="unavailable",
                load_state="unavailable",
                unit_file_state="unavailable",
                error="systemctl_not_available",
            )

        active_code, active_out, active_err = self._run(
            "systemctl",
            "is-active",
            unit,
        )

        enabled_code, enabled_out, enabled_err = self._run(
            "systemctl",
            "is-enabled",
            unit,
        )

        show_code, show_out, show_err = self._run(
            "systemctl",
            "show",
            unit,
            "--property=LoadState,UnitFileState",
            "--no-pager",
        )

        show_values: dict[str, str] = {}

        if show_code == 0:
            for line in show_out.splitlines():
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                show_values[key.strip()] = value.strip()

        errors: list[str] = []

        if (
            active_code not in {0, 3, 4}
            and active_err
        ):
            errors.append(active_err)

        if (
            enabled_code not in {0, 1}
            and enabled_err
        ):
            errors.append(enabled_err)

        if show_code != 0 and show_err:
            errors.append(show_err)

        return UnitInspection(
            unit=unit,
            source_exists=source_exists,
            installed=installed,
            active=self._clean_value(
                active_out,
                active_err,
                "unknown",
            ),
            enabled=self._clean_value(
                enabled_out,
                enabled_err,
                "unknown",
            ),
            load_state=show_values.get(
                "LoadState",
                "unknown",
            ),
            unit_file_state=show_values.get(
                "UnitFileState",
                "unknown",
            ),
            error=" | ".join(
                dict.fromkeys(errors)
            ),
        )

    def inspect(self) -> DeploymentInspection:
        units = [
            self.inspect_unit(unit)
            for unit in UNIT_NAMES
        ]

        units_by_name = {
            item.unit: item
            for item in units
        }

        all_sources_present = all(
            item.source_exists
            for item in units
        )

        all_units_installed = all(
            item.installed
            and item.load_state not in {
                "not-found",
                "unknown",
            }
            for item in units
        )

        monitoring_timers_enabled = all(
            units_by_name.get(timer) is not None
            and units_by_name[timer].enabled
            in {
                "enabled",
                "enabled-runtime",
                "static",
            }
            for timer in MONITORING_TIMERS
        )

        campaign = units_by_name.get(
            CAMPAIGN_SERVICE
        )

        campaign_service_active = bool(
            campaign
            and campaign.active
            in {
                "active",
                "activating",
            }
        )

        campaign_service_enabled = bool(
            campaign
            and campaign.enabled
            in {
                "enabled",
                "enabled-runtime",
            }
        )

        result = DeploymentInspection(
            timestamp=utc_now_iso(),
            systemctl_available=(
                self.systemctl_available()
            ),
            systemd_running=(
                self.systemd_running()
            ),
            all_sources_present=(
                all_sources_present
            ),
            all_units_installed=(
                all_units_installed
            ),
            monitoring_timers_enabled=(
                monitoring_timers_enabled
            ),
            campaign_service_active=(
                campaign_service_active
            ),
            campaign_service_enabled=(
                campaign_service_enabled
            ),
            units=units,
        )

        atomic_write_json(
            self.state_path,
            result.to_dict(),
        )

        return result

    def saved_state(self) -> dict[str, Any]:
        payload = read_json(
            self.state_path,
            default={},
        )

        if not isinstance(payload, dict):
            return {}

        return payload

    def install_sources(self) -> dict[str, Any]:
        missing = [
            unit
            for unit in UNIT_NAMES
            if not (self.source_directory / unit).is_file()
        ]

        if missing:
            return {
                "timestamp": utc_now_iso(),
                "successful": False,
                "installed_units": [],
                "missing_sources": missing,
                "message": (
                    "Required systemd unit files "
                    "were not found."
                ),
                "automatic_promotion": False,
                "automatic_execution": False,
                "order_submission": False,
            }

        self.target_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        installed: list[str] = []

        try:
            for unit in UNIT_NAMES:
                source = self.source_directory / unit
                target = self.target_directory / unit

                shutil.copy2(source, target)
                target.chmod(0o644)

                installed.append(unit)

        except PermissionError as exc:
            return {
                "timestamp": utc_now_iso(),
                "successful": False,
                "installed_units": installed,
                "missing_sources": [],
                "message": (
                    "Administrative privileges "
                    "are required."
                ),
                "error": str(exc),
                "automatic_promotion": False,
                "automatic_execution": False,
                "order_submission": False,
            }

        code, out, err = self._run(
            "systemctl",
            "daemon-reload",
        )

        return {
            "timestamp": utc_now_iso(),
            "successful": code == 0,
            "installed_units": installed,
            "missing_sources": [],
            "daemon_reload_output": out,
            "daemon_reload_error": err,
            "automatic_promotion": False,
            "automatic_execution": False,
            "order_submission": False,
        }
