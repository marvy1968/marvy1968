from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from segment4b4.campaign_state import CampaignState
from segment4b4.common import (
    atomic_write_json,
    load_segment4b4_config,
    resolve_path,
    utc_now_iso,
)


@dataclass(slots=True)
class CampaignHealthResult:
    timestamp: str
    healthy: bool
    stalled: bool
    severity: str
    state: str
    running: bool
    status_age_seconds: float | None
    cycle_number: int
    consecutive_errors: int
    open_shadow_trades: int
    closed_shadow_trades: int
    checkpoint: str
    reasons: list[str]
    warnings: list[str]
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_timestamp(
    value: Any,
) -> datetime | None:
    if not isinstance(value, str):
        return None

    value = value.strip()

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


class CampaignHealthMonitor:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, dict)
            else load_segment4b4_config()
        )

        self.state = CampaignState(
            self.config
        )

        operations = self.config.get(
            "operations",
            {},
        )

        if not isinstance(
            operations,
            dict,
        ):
            operations = {}

        self.stall_seconds = max(
            60,
            int(
                operations.get(
                    "campaign_stall_seconds",
                    900,
                )
            ),
        )

        self.maximum_errors = max(
            1,
            int(
                operations.get(
                    "maximum_consecutive_errors",
                    3,
                )
            ),
        )

        self.health_path = resolve_path(
            self.config,
            "campaign_health",
        )

    def evaluate(
        self,
    ) -> CampaignHealthResult:
        status = self.state.load()

        state_name = str(
            status.get(
                "state",
                "unknown",
            )
        )

        running = bool(
            status.get(
                "running",
                False,
            )
        )

        cycle_number = int(
            status.get(
                "cycle_number",
                0,
            )
            or 0
        )

        consecutive_errors = int(
            status.get(
                "consecutive_errors",
                0,
            )
            or 0
        )

        timestamp = parse_timestamp(
            status.get(
                "timestamp"
            )
        )

        age_seconds: float | None = None

        if timestamp is not None:
            age_seconds = max(
                0.0,
                (
                    datetime.now(
                        timezone.utc
                    )
                    - timestamp
                ).total_seconds(),
            )

        reasons: list[str] = []
        warnings: list[str] = []

        stalled = bool(
            running
            and (
                age_seconds is None
                or age_seconds
                > self.stall_seconds
            )
        )

        if stalled:
            reasons.append(
                "campaign_status_stale"
            )

        if (
            consecutive_errors
            >= self.maximum_errors
        ):
            reasons.append(
                "consecutive_error_limit_reached"
            )

        if state_name in {
            "cycle_failed",
            "checkpoint_hold",
        }:
            warnings.append(
                f"campaign_state_{state_name}"
            )

        if bool(
            status.get(
                "campaign_complete",
                False,
            )
        ):
            warnings.append(
                "campaign_target_complete"
            )

        if self.state.paused():
            warnings.append(
                "campaign_paused"
            )

        if self.state.shutdown_requested():
            warnings.append(
                "shutdown_requested"
            )

        healthy = (
            len(reasons) == 0
        )

        severity = (
            "critical"
            if reasons
            else (
                "warning"
                if warnings
                else "info"
            )
        )

        result = CampaignHealthResult(
            timestamp=utc_now_iso(),
            healthy=healthy,
            stalled=stalled,
            severity=severity,
            state=state_name,
            running=running,
            status_age_seconds=age_seconds,
            cycle_number=cycle_number,
            consecutive_errors=consecutive_errors,
            open_shadow_trades=int(
                status.get(
                    "open_shadow_trades",
                    0,
                )
                or 0
            ),
            closed_shadow_trades=int(
                status.get(
                    "closed_shadow_trades",
                    0,
                )
                or 0
            ),
            checkpoint=str(
                status.get(
                    "checkpoint",
                    "data_collection",
                )
            ),
            reasons=reasons,
            warnings=warnings,
        )

        atomic_write_json(
            self.health_path,
            result.to_dict(),
        )

        return result
