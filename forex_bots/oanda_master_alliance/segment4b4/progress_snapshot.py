from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    ROOT,
    append_jsonl,
    atomic_write_json,
    load_segment4b4_config,
    read_json,
    utc_now_iso,
)


@dataclass(slots=True)
class CampaignProgressSnapshot:
    timestamp: str
    snapshot_date: str
    state: str
    running: bool
    cycle_number: int
    checkpoint: str
    previous_checkpoint: str
    checkpoint_changed: bool
    campaign_complete: bool
    open_shadow_trades: int
    closed_shadow_trades: int
    target_closed_trades: int
    completion_percent: float
    remaining_trades: int
    last_cycle_successful: bool | None
    consecutive_errors: int
    paused: bool
    shutdown_requested: bool
    alert_created: bool
    alert_event: str
    automatic_promotion: bool = False
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CampaignProgressRecorder:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_segment4b4_config()
        )

        self.state = CampaignState(
            self.config
        )

        self.base_path = (
            ROOT
            / "state/shadow_campaign"
        )

        self.snapshot_directory = (
            self.base_path
            / "snapshots"
        )

        self.latest_snapshot_path = (
            self.base_path
            / "latest_progress_snapshot.json"
        )

        self.snapshot_history_path = (
            self.base_path
            / "progress_history.jsonl"
        )

        self.checkpoint_alert_path = (
            self.base_path
            / "checkpoint_alerts.jsonl"
        )

        self.latest_alert_path = (
            self.base_path
            / "latest_checkpoint_alert.json"
        )

    @staticmethod
    def _safe_int(
        value: Any,
        default: int = 0,
    ) -> int:
        if isinstance(
            value,
            bool,
        ):
            return default

        try:
            return max(
                0,
                int(value),
            )
        except (
            TypeError,
            ValueError,
        ):
            return default

    @staticmethod
    def _checkpoint_rank(
        checkpoint: str,
    ) -> int:
        ranks = {
            "data_collection": 0,
            "observation": 1,
            "provisional": 2,
            "advanced": 3,
            "production_review": 4,
        }

        return ranks.get(
            checkpoint,
            -1,
        )

    def _previous_snapshot(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.latest_snapshot_path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        return payload

    def _create_checkpoint_alert(
        self,
        *,
        checkpoint: str,
        previous_checkpoint: str,
        closed_trades: int,
        target_trades: int,
        campaign_complete: bool,
    ) -> tuple[bool, str]:
        checkpoint_changed = (
            checkpoint
            != previous_checkpoint
        )

        advanced_forward = (
            checkpoint_changed
            and self._checkpoint_rank(
                checkpoint
            )
            > self._checkpoint_rank(
                previous_checkpoint
            )
        )

        if campaign_complete:
            event_type = (
                "campaign_target_reached"
            )

        elif advanced_forward:
            event_type = (
                f"checkpoint_reached_{checkpoint}"
            )

        else:
            return False, ""

        alert = {
            "timestamp": utc_now_iso(),
            "event_type": event_type,
            "checkpoint": checkpoint,
            "previous_checkpoint": (
                previous_checkpoint
            ),
            "closed_shadow_trades": (
                closed_trades
            ),
            "target_closed_trades": (
                target_trades
            ),
            "campaign_complete": (
                campaign_complete
            ),
            "severity": (
                "warning"
                if campaign_complete
                else "info"
            ),
            "delivered": False,
            "delivery_mode": (
                "local_tracking_only"
            ),
            "message": (
                "Shadow campaign reached "
                f"{checkpoint}."
            ),
            "automatic_promotion": False,
            "automatic_execution": False,
            "order_submission": False,
        }

        append_jsonl(
            self.checkpoint_alert_path,
            alert,
        )

        atomic_write_json(
            self.latest_alert_path,
            alert,
        )

        return True, event_type

    def create(
        self,
    ) -> CampaignProgressSnapshot:
        status = self.state.load()
        previous = self._previous_snapshot()

        checkpoint = str(
            status.get(
                "checkpoint",
                "data_collection",
            )
        )

        previous_checkpoint = str(
            previous.get(
                "checkpoint",
                checkpoint,
            )
        )

        open_trades = self._safe_int(
            status.get(
                "open_shadow_trades",
                0,
            )
        )

        closed_trades = self._safe_int(
            status.get(
                "closed_shadow_trades",
                0,
            )
        )

        target_trades = max(
            1,
            self._safe_int(
                status.get(
                    "target_closed_trades",
                    250,
                ),
                250,
            ),
        )

        campaign_complete = bool(
            status.get(
                "campaign_complete",
                False,
            )
            or closed_trades
            >= target_trades
        )

        completion_percent = round(
            min(
                100.0,
                (
                    closed_trades
                    / target_trades
                )
                * 100.0,
            ),
            2,
        )

        remaining_trades = max(
            0,
            target_trades
            - closed_trades,
        )

        alert_created, alert_event = (
            self._create_checkpoint_alert(
                checkpoint=checkpoint,
                previous_checkpoint=(
                    previous_checkpoint
                ),
                closed_trades=closed_trades,
                target_trades=target_trades,
                campaign_complete=(
                    campaign_complete
                ),
            )
        )

        now = datetime.now(
            timezone.utc
        )

        snapshot = CampaignProgressSnapshot(
            timestamp=utc_now_iso(),
            snapshot_date=(
                now.date().isoformat()
            ),
            state=str(
                status.get(
                    "state",
                    "unknown",
                )
            ),
            running=bool(
                status.get(
                    "running",
                    False,
                )
            ),
            cycle_number=self._safe_int(
                status.get(
                    "cycle_number",
                    0,
                )
            ),
            checkpoint=checkpoint,
            previous_checkpoint=(
                previous_checkpoint
            ),
            checkpoint_changed=(
                checkpoint
                != previous_checkpoint
            ),
            campaign_complete=(
                campaign_complete
            ),
            open_shadow_trades=(
                open_trades
            ),
            closed_shadow_trades=(
                closed_trades
            ),
            target_closed_trades=(
                target_trades
            ),
            completion_percent=(
                completion_percent
            ),
            remaining_trades=(
                remaining_trades
            ),
            last_cycle_successful=(
                status.get(
                    "last_cycle_successful"
                )
            ),
            consecutive_errors=(
                self._safe_int(
                    status.get(
                        "consecutive_errors",
                        0,
                    )
                )
            ),
            paused=bool(
                status.get(
                    "paused",
                    False,
                )
            ),
            shutdown_requested=bool(
                status.get(
                    "shutdown_requested",
                    False,
                )
            ),
            alert_created=(
                alert_created
            ),
            alert_event=alert_event,
        )

        payload = snapshot.to_dict()

        self.snapshot_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        dated_path = (
            self.snapshot_directory
            / (
                f"campaign_progress_"
                f"{snapshot.snapshot_date}.json"
            )
        )

        atomic_write_json(
            dated_path,
            payload,
        )

        atomic_write_json(
            self.latest_snapshot_path,
            payload,
        )

        append_jsonl(
            self.snapshot_history_path,
            payload,
        )

        return snapshot
