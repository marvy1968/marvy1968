from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    load_segment4b4_config,
    read_json,
    resolve_path,
)


def load_payload(
    path: Path,
) -> dict[str, Any]:
    payload = read_json(
        path,
        default={},
    )

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    return payload


def build_summary() -> dict[str, Any]:
    config = load_segment4b4_config()

    state = CampaignState(
        config
    )

    campaign_status = state.load()

    health = load_payload(
        resolve_path(
            config,
            "campaign_health",
        )
    )

    base_path = (
        ROOT
        / "state/shadow_campaign"
    )

    snapshot = load_payload(
        base_path
        / "latest_progress_snapshot.json"
    )

    alert = load_payload(
        base_path
        / "latest_checkpoint_alert.json"
    )

    delivery_state = load_payload(
        base_path
        / "checkpoint_alert_delivery_state.json"
    )

    return {
        "campaign_state": campaign_status.get(
            "state",
            "unknown",
        ),
        "campaign_running": campaign_status.get(
            "running",
            False,
        ),
        "cycle_number": campaign_status.get(
            "cycle_number",
            0,
        ),
        "checkpoint": campaign_status.get(
            "checkpoint",
            "data_collection",
        ),
        "closed_shadow_trades": (
            campaign_status.get(
                "closed_shadow_trades",
                0,
            )
        ),
        "target_closed_trades": (
            campaign_status.get(
                "target_closed_trades",
                250,
            )
        ),
        "campaign_complete": (
            campaign_status.get(
                "campaign_complete",
                False,
            )
        ),
        "health_present": bool(
            health
        ),
        "healthy": health.get(
            "healthy",
        ),
        "stalled": health.get(
            "stalled",
        ),
        "health_severity": health.get(
            "severity",
            "unknown",
        ),
        "health_reasons": health.get(
            "reasons",
            [],
        ),
        "snapshot_present": bool(
            snapshot
        ),
        "completion_percent": snapshot.get(
            "completion_percent",
            0.0,
        ),
        "remaining_trades": snapshot.get(
            "remaining_trades",
            250,
        ),
        "latest_alert_present": bool(
            alert
        ),
        "latest_alert_event": alert.get(
            "event_type",
            "",
        ),
        "latest_alert_delivered": alert.get(
            "delivered",
            False,
        ),
        "last_delivered_alert_id": (
            delivery_state.get(
                "last_delivered_alert_id",
                "",
            )
        ),
        "paused": state.paused(),
        "shutdown_requested": (
            state.shutdown_requested()
        ),
        "automatic_promotion": False,
        "automatic_execution": False,
        "order_submission": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Display Segment 4B-4 "
            "campaign operations state."
        )
    )

    parser.add_argument(
        "view",
        nargs="?",
        default="summary",
        choices=[
            "summary",
            "health",
            "snapshot",
            "alert",
        ],
    )

    arguments = parser.parse_args()

    config = load_segment4b4_config()

    base_path = (
        ROOT
        / "state/shadow_campaign"
    )

    if arguments.view == "health":
        payload = load_payload(
            resolve_path(
                config,
                "campaign_health",
            )
        )

    elif arguments.view == "snapshot":
        payload = load_payload(
            base_path
            / "latest_progress_snapshot.json"
        )

    elif arguments.view == "alert":
        payload = load_payload(
            base_path
            / "latest_checkpoint_alert.json"
        )

    else:
        payload = build_summary()

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
