from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from segment4b4.campaign_state import CampaignState
from segment4b4.common import load_segment4b4_config


def build_summary(
    state: CampaignState,
) -> dict:
    status = state.load()

    return {
        "state": status.get(
            "state",
            "unknown",
        ),
        "running": status.get(
            "running",
            False,
        ),
        "cycle_number": status.get(
            "cycle_number",
            0,
        ),
        "checkpoint": status.get(
            "checkpoint",
            "data_collection",
        ),
        "campaign_complete": status.get(
            "campaign_complete",
            False,
        ),
        "open_shadow_trades": status.get(
            "open_shadow_trades",
            0,
        ),
        "closed_shadow_trades": status.get(
            "closed_shadow_trades",
            0,
        ),
        "target_closed_trades": status.get(
            "target_closed_trades",
            250,
        ),
        "last_cycle_successful": status.get(
            "last_cycle_successful",
        ),
        "consecutive_errors": status.get(
            "consecutive_errors",
            0,
        ),
        "paused": status.get(
            "paused",
            False,
        ),
        "shutdown_requested": status.get(
            "shutdown_requested",
            False,
        ),
        "automatic_execution": False,
        "order_submission": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "view",
        choices=[
            "summary",
            "status",
        ],
        nargs="?",
        default="summary",
    )

    arguments = parser.parse_args()

    config = load_segment4b4_config()
    state = CampaignState(config)

    if arguments.view == "status":
        payload = state.load()
    else:
        payload = build_summary(state)

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
