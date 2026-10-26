from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class CheckpointDecision:
    checkpoint: str
    closed_trades: int
    target_trades: int
    campaign_complete: bool
    stop_requested: bool
    reason: str
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CampaignCheckpointManager:
    def __init__(
        self,
        config: dict[str, Any],
        control: dict[str, Any],
    ) -> None:
        campaign = config.get(
            "campaign",
            {},
        )

        if not isinstance(campaign, dict):
            campaign = {}

        self.control = (
            control
            if isinstance(control, dict)
            else {}
        )

        self.observation = int(
            campaign.get(
                "observation_checkpoint",
                30,
            )
        )

        self.provisional = int(
            campaign.get(
                "provisional_checkpoint",
                50,
            )
        )

        self.advanced = int(
            campaign.get(
                "advanced_checkpoint",
                100,
            )
        )

        self.production = int(
            campaign.get(
                "production_review_checkpoint",
                250,
            )
        )

        self.target = int(
            campaign.get(
                "target_closed_trades",
                self.production,
            )
        )

    def evaluate(
        self,
        closed_trades: int,
    ) -> CheckpointDecision:
        closed_trades = max(
            0,
            int(closed_trades),
        )

        if closed_trades >= self.production:
            checkpoint = "production_review"

        elif closed_trades >= self.advanced:
            checkpoint = "advanced"

        elif closed_trades >= self.provisional:
            checkpoint = "provisional"

        elif closed_trades >= self.observation:
            checkpoint = "observation"

        else:
            checkpoint = "data_collection"

        campaign_complete = (
            closed_trades >= self.target
        )

        stop_flags = {
            "observation": bool(
                self.control.get(
                    "stop_at_observation_checkpoint",
                    False,
                )
            ),
            "provisional": bool(
                self.control.get(
                    "stop_at_provisional_checkpoint",
                    False,
                )
            ),
            "advanced": bool(
                self.control.get(
                    "stop_at_advanced_checkpoint",
                    False,
                )
            ),
            "production_review": bool(
                self.control.get(
                    "stop_at_production_review_checkpoint",
                    True,
                )
            ),
        }

        stop_requested = (
            campaign_complete
            or stop_flags.get(
                checkpoint,
                False,
            )
        )

        if campaign_complete:
            reason = "campaign_target_reached"

        elif stop_requested:
            reason = (
                f"{checkpoint}_checkpoint_stop"
            )

        else:
            reason = "continue_campaign"

        return CheckpointDecision(
            checkpoint=checkpoint,
            closed_trades=closed_trades,
            target_trades=self.target,
            campaign_complete=campaign_complete,
            stop_requested=stop_requested,
            reason=reason,
        )
