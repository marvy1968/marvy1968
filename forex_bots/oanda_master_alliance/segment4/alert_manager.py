from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from core.common import ROOT
from segment4.deployment_common import (
    append_jsonl,
    atomic_write_json,
    deployment_paths,
    load_segment4a3_config,
    read_json,
    utc_now_iso,
)


SEVERITY_ORDER = {
    "info": 10,
    "warning": 20,
    "error": 30,
    "critical": 40,
}


@dataclass
class AlertResult:
    timestamp: str
    alert_id: str
    event_type: str
    severity: str
    title: str
    message: str
    delivered: bool
    suppressed: bool
    delivery_channels: list[str]
    errors: list[str]
    metadata: dict[str, Any]
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


class AlertManager:
    def __init__(
        self,
    ) -> None:
        root_config = load_segment4a3_config()

        self.config = root_config.get(
            "alerts",
            {},
        )

        if not isinstance(
            self.config,
            dict,
        ):
            self.config = {}

        self.paths = deployment_paths()

    @staticmethod
    def _parse_time(
        value: Any,
    ) -> datetime | None:
        if not isinstance(
            value,
            str,
        ):
            return None

        try:
            return datetime.fromisoformat(
                value
            )

        except ValueError:
            return None

    @staticmethod
    def _alert_id(
        event_type: str,
        severity: str,
        title: str,
        message: str,
    ) -> str:
        material = "|".join(
            [
                event_type,
                severity,
                title,
                message,
            ]
        )

        return hashlib.sha256(
            material.encode(
                "utf-8"
            )
        ).hexdigest()[:20]

    def _registry(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.paths[
                "alert_registry"
            ],
            default={
                "updated_at": None,
                "alerts": {},
            },
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {
                "updated_at": None,
                "alerts": {},
            }

        alerts = payload.get(
            "alerts",
            {},
        )

        if not isinstance(
            alerts,
            dict,
        ):
            payload[
                "alerts"
            ] = {}

        return payload

    def _severity_allowed(
        self,
        severity: str,
    ) -> bool:
        minimum = str(
            self.config.get(
                "minimum_severity",
                "warning",
            )
        ).lower()

        return (
            SEVERITY_ORDER.get(
                severity,
                0,
            )
            >= SEVERITY_ORDER.get(
                minimum,
                20,
            )
        )

    def _cooldown_active(
        self,
        *,
        alert_id: str,
        severity: str,
        registry: dict[str, Any],
    ) -> bool:
        alerts = registry.get(
            "alerts",
            {},
        )

        if not isinstance(
            alerts,
            dict,
        ):
            return False

        existing = alerts.get(
            alert_id,
            {},
        )

        if not isinstance(
            existing,
            dict,
        ):
            return False

        delivered_at = self._parse_time(
            existing.get(
                "last_delivered_at"
            )
        )

        if delivered_at is None:
            return False

        if severity == "critical":
            minutes = float(
                self.config.get(
                    "repeat_critical_after_minutes",
                    60,
                )
            )
        else:
            minutes = float(
                self.config.get(
                    "cooldown_minutes",
                    30,
                )
            )

        threshold = (
            datetime.now().astimezone()
            - timedelta(
                minutes=max(
                    0.0,
                    minutes,
                )
            )
        )

        return (
            delivered_at
            > threshold
        )

    def _send_telegram(
        self,
        *,
        title: str,
        message: str,
        severity: str,
    ) -> tuple[
        bool,
        str,
    ]:
        token_environment = str(
            self.config.get(
                (
                    "telegram_bot_token_"
                    "environment"
                ),
                "TELEGRAM_BOT_TOKEN",
            )
        )

        chat_environment = str(
            self.config.get(
                (
                    "telegram_chat_id_"
                    "environment"
                ),
                "TELEGRAM_CHAT_ID",
            )
        )

        token = os.getenv(
            token_environment,
            "",
        ).strip()

        chat_id = os.getenv(
            chat_environment,
            "",
        ).strip()

        if not token or not chat_id:
            return (
                False,
                (
                    "Telegram credentials "
                    "are missing."
                ),
            )

        text = (
            f"[{severity.upper()}] "
            f"{title}\n\n"
            f"{message}"
        )

        try:
            response = requests.post(
                (
                    "https://api.telegram.org/"
                    f"bot{token}/sendMessage"
                ),
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )

            if response.status_code != 200:
                return (
                    False,
                    (
                        "Telegram HTTP "
                        f"{response.status_code}: "
                        f"{response.text[:500]}"
                    ),
                )

            return (
                True,
                "",
            )

        except requests.RequestException as exc:
            return (
                False,
                (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )

    def send(
        self,
        *,
        event_type: str,
        severity: str,
        title: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        force: bool = False,
    ) -> AlertResult:
        severity = severity.lower().strip()

        if severity not in SEVERITY_ORDER:
            raise ValueError(
                f"Unsupported severity: {severity}"
            )

        timestamp = utc_now_iso()

        alert_id = self._alert_id(
            event_type,
            severity,
            title,
            message,
        )

        registry = self._registry()

        suppressed = False
        delivered = False
        delivery_channels: list[
            str
        ] = []
        errors: list[
            str
        ] = []

        enabled = bool(
            self.config.get(
                "enabled",
                True,
            )
        )

        if not enabled:
            suppressed = True
            errors.append(
                "Alert system is disabled."
            )

        elif (
            not force
            and not self._severity_allowed(
                severity
            )
        ):
            suppressed = True
            errors.append(
                "Severity is below configured minimum."
            )

        elif (
            not force
            and self._cooldown_active(
                alert_id=alert_id,
                severity=severity,
                registry=registry,
            )
        ):
            suppressed = True
            errors.append(
                "Alert cooldown is active."
            )

        else:
            if bool(
                self.config.get(
                    "console_enabled",
                    True,
                )
            ):
                print()
                print("=" * 72)
                print(
                    f"OANDA MASTER AI ALERT — "
                    f"{severity.upper()}"
                )
                print("=" * 72)
                print(
                    title
                )
                print()
                print(
                    message
                )
                print("=" * 72)

                delivery_channels.append(
                    "console"
                )

            if bool(
                self.config.get(
                    "file_enabled",
                    True,
                )
            ):
                delivery_channels.append(
                    "file"
                )

            if bool(
                self.config.get(
                    "telegram_enabled",
                    False,
                )
            ):
                telegram_passed, telegram_error = (
                    self._send_telegram(
                        title=title,
                        message=message,
                        severity=severity,
                    )
                )

                if telegram_passed:
                    delivery_channels.append(
                        "telegram"
                    )
                else:
                    errors.append(
                        telegram_error
                    )

            delivered = bool(
                delivery_channels
            )

        result = AlertResult(
            timestamp=timestamp,
            alert_id=alert_id,
            event_type=event_type,
            severity=severity,
            title=title,
            message=message,
            delivered=delivered,
            suppressed=suppressed,
            delivery_channels=(
                delivery_channels
            ),
            errors=errors,
            metadata=(
                metadata
                if isinstance(
                    metadata,
                    dict,
                )
                else {}
            ),
        )

        append_jsonl(
            self.paths[
                "alert_history"
            ],
            result.to_dict(),
        )

        atomic_write_json(
            self.paths[
                "alert_status"
            ],
            result.to_dict(),
        )

        alerts = registry.setdefault(
            "alerts",
            {},
        )

        if not isinstance(
            alerts,
            dict,
        ):
            alerts = {}
            registry[
                "alerts"
            ] = alerts

        existing = alerts.get(
            alert_id,
            {},
        )

        if not isinstance(
            existing,
            dict,
        ):
            existing = {}

        existing.update(
            {
                "alert_id": alert_id,
                "event_type": event_type,
                "severity": severity,
                "title": title,
                "last_attempted_at": (
                    timestamp
                ),
                "last_delivered_at": (
                    timestamp
                    if delivered
                    else existing.get(
                        "last_delivered_at"
                    )
                ),
                "attempt_count": int(
                    existing.get(
                        "attempt_count",
                        0,
                    )
                )
                + 1,
                "delivery_count": int(
                    existing.get(
                        "delivery_count",
                        0,
                    )
                )
                + (
                    1
                    if delivered
                    else 0
                ),
                "suppressed_count": int(
                    existing.get(
                        "suppressed_count",
                        0,
                    )
                )
                + (
                    1
                    if suppressed
                    else 0
                ),
            }
        )

        alerts[
            alert_id
        ] = existing

        registry[
            "updated_at"
        ] = timestamp

        atomic_write_json(
            self.paths[
                "alert_registry"
            ],
            registry,
        )

        return result

    def evaluate_operations(
        self,
    ) -> list[AlertResult]:
        alert_on = self.config.get(
            "alert_on",
            {},
        )

        if not isinstance(
            alert_on,
            dict,
        ):
            alert_on = {}

        health = read_json(
            ROOT
            / "state/segment4/"
            "health_status.json",
            default={},
        )

        recovery = read_json(
            ROOT
            / "state/recovery/"
            "recovery_status.json",
            default={},
        )

        watchdog = read_json(
            ROOT
            / "state/segment4/"
            "watchdog_status.json",
            default={},
        )

        backup = read_json(
            self.paths[
                "backup_status"
            ],
            default={},
        )

        maintenance = read_json(
            self.paths[
                "maintenance_status"
            ],
            default={},
        )

        emitted: list[
            AlertResult
        ] = []

        if (
            bool(
                alert_on.get(
                    "health_failure",
                    True,
                )
            )
            and isinstance(
                health,
                dict,
            )
            and health
            and not bool(
                health.get(
                    "healthy",
                    False,
                )
            )
        ):
            emitted.append(
                self.send(
                    event_type="health_failure",
                    severity=(
                        "critical"
                        if int(
                            health.get(
                                "critical_failures",
                                0,
                            )
                        )
                        > 0
                        else "warning"
                    ),
                    title=(
                        "Segment 4 health failure"
                    ),
                    message=(
                        "The runtime health monitor "
                        "reported an unhealthy state. "
                        f"Critical failures: "
                        f"{health.get('critical_failures', 0)}. "
                        f"Warnings: "
                        f"{health.get('warnings', 0)}."
                    ),
                    metadata=health,
                )
            )

        if (
            bool(
                alert_on.get(
                    "recovery_block",
                    True,
                )
            )
            and isinstance(
                recovery,
                dict,
            )
            and int(
                recovery.get(
                    "unresolved_blockers",
                    0,
                )
            )
            > 0
        ):
            emitted.append(
                self.send(
                    event_type="recovery_block",
                    severity="critical",
                    title=(
                        "Segment 4 startup recovery block"
                    ),
                    message=(
                        "Runtime startup is blocked by "
                        f"{recovery.get('unresolved_blockers')} "
                        "unresolved recovery condition(s)."
                    ),
                    metadata=recovery,
                )
            )

        if (
            bool(
                alert_on.get(
                    "watchdog_failure",
                    True,
                )
            )
            and isinstance(
                watchdog,
                dict,
            )
            and watchdog.get(
                "state"
            )
            in {
                "failed",
                "unhealthy",
                "restart_failed",
            }
        ):
            emitted.append(
                self.send(
                    event_type="watchdog_failure",
                    severity="critical",
                    title=(
                        "Segment 4 watchdog failure"
                    ),
                    message=(
                        "The operations watchdog reported "
                        f"state={watchdog.get('state')}. "
                        f"Action={watchdog.get('action')}. "
                        f"Reason={watchdog.get('action_reason') or 'none'}."
                    ),
                    metadata=watchdog,
                )
            )

        if (
            bool(
                alert_on.get(
                    "backup_failure",
                    True,
                )
            )
            and isinstance(
                backup,
                dict,
            )
            and backup
            and backup.get(
                "successful"
            )
            is False
        ):
            emitted.append(
                self.send(
                    event_type="backup_failure",
                    severity="error",
                    title=(
                        "OANDA Master AI backup failed"
                    ),
                    message=(
                        backup.get(
                            "error",
                            "Backup failed without an error message.",
                        )
                    ),
                    metadata=backup,
                )
            )

        if (
            bool(
                alert_on.get(
                    "maintenance_failure",
                    True,
                )
            )
            and isinstance(
                maintenance,
                dict,
            )
            and maintenance
            and maintenance.get(
                "successful"
            )
            is False
        ):
            emitted.append(
                self.send(
                    event_type="maintenance_failure",
                    severity="error",
                    title=(
                        "OANDA Master AI maintenance failed"
                    ),
                    message=(
                        "One or more maintenance or "
                        "integrity checks failed."
                    ),
                    metadata=maintenance,
                )
            )

        return emitted
