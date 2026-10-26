from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from segment4b4.common import (
    ROOT,
    append_jsonl,
    atomic_write_json,
    read_json,
    utc_now_iso,
)


@dataclass(slots=True)
class CheckpointAlertResult:
    timestamp: str
    alert_present: bool
    delivered: bool
    suppressed: bool
    alert_id: str
    event_type: str
    checkpoint: str
    severity: str
    message: str
    delivery_channels: list[str]
    reasons: list[str]
    automatic_promotion: bool = False
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointAlertManager:
    def __init__(self) -> None:
        self.base_path = (
            ROOT
            / "state/shadow_campaign"
        )

        self.latest_alert_path = (
            self.base_path
            / "latest_checkpoint_alert.json"
        )

        self.delivery_state_path = (
            self.base_path
            / "checkpoint_alert_delivery_state.json"
        )

        self.delivery_history_path = (
            self.base_path
            / "checkpoint_alert_delivery_history.jsonl"
        )

        self.alert_log_path = (
            ROOT
            / "logs/shadow_campaign_checkpoint_alerts.log"
        )

    @staticmethod
    def _fingerprint(
        payload: dict[str, Any],
    ) -> str:
        source = "|".join(
            [
                str(
                    payload.get(
                        "event_type",
                        "",
                    )
                ),
                str(
                    payload.get(
                        "checkpoint",
                        "",
                    )
                ),
                str(
                    payload.get(
                        "closed_shadow_trades",
                        0,
                    )
                ),
                str(
                    payload.get(
                        "target_closed_trades",
                        0,
                    )
                ),
            ]
        )

        return hashlib.sha256(
            source.encode(
                "utf-8"
            )
        ).hexdigest()[:20]

    def _delivery_state(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.delivery_state_path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        return payload

    def deliver(
        self,
        *,
        force: bool = False,
    ) -> CheckpointAlertResult:
        alert = read_json(
            self.latest_alert_path,
            default={},
        )

        if not isinstance(
            alert,
            dict,
        ) or not alert:
            result = CheckpointAlertResult(
                timestamp=utc_now_iso(),
                alert_present=False,
                delivered=False,
                suppressed=False,
                alert_id="",
                event_type="",
                checkpoint="",
                severity="info",
                message=(
                    "No checkpoint alert is available."
                ),
                delivery_channels=[],
                reasons=[
                    "no_checkpoint_alert",
                ],
            )

            append_jsonl(
                self.delivery_history_path,
                result.to_dict(),
            )

            return result

        alert_id = self._fingerprint(
            alert
        )

        delivery_state = (
            self._delivery_state()
        )

        delivered_ids = (
            delivery_state.get(
                "delivered_alert_ids",
                [],
            )
        )

        if not isinstance(
            delivered_ids,
            list,
        ):
            delivered_ids = []

        duplicate = (
            alert_id in delivered_ids
        )

        event_type = str(
            alert.get(
                "event_type",
                "checkpoint_event",
            )
        )

        checkpoint = str(
            alert.get(
                "checkpoint",
                "unknown",
            )
        )

        severity = str(
            alert.get(
                "severity",
                "info",
            )
        )

        message = str(
            alert.get(
                "message",
                (
                    "Shadow campaign checkpoint "
                    f"event: {checkpoint}"
                ),
            )
        )

        if duplicate and not force:
            result = CheckpointAlertResult(
                timestamp=utc_now_iso(),
                alert_present=True,
                delivered=False,
                suppressed=True,
                alert_id=alert_id,
                event_type=event_type,
                checkpoint=checkpoint,
                severity=severity,
                message=message,
                delivery_channels=[],
                reasons=[
                    "duplicate_alert_suppressed",
                ],
            )

            append_jsonl(
                self.delivery_history_path,
                result.to_dict(),
            )

            return result

        display = [
            "",
            "=" * 72,
            (
                "OANDA MASTER AI SHADOW "
                f"CAMPAIGN ALERT — "
                f"{severity.upper()}"
            ),
            "=" * 72,
            message,
            (
                "Checkpoint: "
                f"{checkpoint}"
            ),
            (
                "Closed shadow trades: "
                f"{alert.get('closed_shadow_trades', 0)}"
            ),
            (
                "Target shadow trades: "
                f"{alert.get('target_closed_trades', 0)}"
            ),
            (
                "Automatic execution: false"
            ),
            (
                "Orders submitted: 0"
            ),
            "=" * 72,
            "",
        ]

        console_message = "\n".join(
            display
        )

        print(
            console_message,
            flush=True,
        )

        self.alert_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self.alert_log_path.open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(
                console_message
            )

        if alert_id not in delivered_ids:
            delivered_ids.append(
                alert_id
            )

        atomic_write_json(
            self.delivery_state_path,
            {
                "updated_at": utc_now_iso(),
                "last_delivered_alert_id": (
                    alert_id
                ),
                "delivered_alert_ids": (
                    delivered_ids[-500:]
                ),
                "automatic_promotion": False,
                "automatic_execution": False,
                "order_submission": False,
            },
        )

        result = CheckpointAlertResult(
            timestamp=utc_now_iso(),
            alert_present=True,
            delivered=True,
            suppressed=False,
            alert_id=alert_id,
            event_type=event_type,
            checkpoint=checkpoint,
            severity=severity,
            message=message,
            delivery_channels=[
                "console",
                "file",
            ],
            reasons=[],
        )

        append_jsonl(
            self.delivery_history_path,
            result.to_dict(),
        )

        delivered_alert = {
            **alert,
            "alert_id": alert_id,
            "delivered": True,
            "delivered_at": (
                result.timestamp
            ),
            "delivery_channels": (
                result.delivery_channels
            ),
            "automatic_promotion": False,
            "automatic_execution": False,
            "order_submission": False,
        }

        atomic_write_json(
            self.latest_alert_path,
            delivered_alert,
        )

        return result
