from __future__ import annotations

import argparse
import json
import time
from typing import Any

from segment4b4.campaign_engine import (
    ShadowCampaignEngine,
)
from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    load_segment4b4_config,
)


def print_result(
    result: dict[str, Any],
) -> None:
    print(
        json.dumps(
            result,
            indent=2,
        ),
        flush=True,
    )

    print()
    print(
        "Segment 4B-4 shadow campaign "
        "cycle completed."
    )
    print(
        "Automatic execution: false"
    )
    print(
        "Orders submitted: 0"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the research-only "
            "Segment 4B-4 shadow campaign."
        )
    )

    parser.add_argument(
        "--daemon",
        action="store_true",
        help=(
            "Run repeated foreground "
            "campaign cycles."
        ),
    )

    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        help=(
            "Override the configured delay "
            "between campaign cycles."
        ),
    )

    parser.add_argument(
        "--maximum-cycles",
        type=int,
        default=0,
        help=(
            "Stop after this many cycles. "
            "Zero means no explicit limit."
        ),
    )

    arguments = parser.parse_args()

    config = load_segment4b4_config()

    campaign = config.get(
        "campaign",
        {},
    )

    if not isinstance(
        campaign,
        dict,
    ):
        campaign = {}

    interval_seconds = max(
        1,
        int(
            arguments.interval_seconds
            if arguments.interval_seconds
            is not None
            else campaign.get(
                "cycle_interval_seconds",
                60,
            )
        ),
    )

    maximum_cycles = max(
        0,
        int(
            arguments.maximum_cycles
        ),
    )

    engine = ShadowCampaignEngine(
        config
    )

    state = CampaignState(
        config
    )

    if not arguments.daemon:
        result = (
            engine.run_locked_cycle()
        )

        print_result(
            result
        )

        return

    completed_cycles = 0

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-4 CAMPAIGN"
    )
    print("=" * 72)
    print(
        "Mode: foreground research campaign"
    )
    print(
        f"Cycle interval: "
        f"{interval_seconds} seconds"
    )
    print(
        "Automatic execution: false"
    )
    print(
        "Order submission: false"
    )
    print()

    while True:
        if state.shutdown_requested():
            print(
                "Campaign shutdown request "
                "detected."
            )
            break

        try:
            result = (
                engine.run_locked_cycle()
            )
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        "successful": False,
                        "error": str(exc),
                        "automatic_execution": (
                            False
                        ),
                        "order_submission": (
                            False
                        ),
                    },
                    indent=2,
                ),
                flush=True,
            )

            break

        completed_cycles += 1

        print_result(
            result
        )

        if bool(
            result.get(
                "campaign_complete",
                False,
            )
        ):
            print(
                "Campaign target reached."
            )
            break

        if bool(
            result.get(
                "checkpoint_stop_requested",
                False,
            )
        ):
            print(
                "Campaign stopped at a "
                "configured checkpoint."
            )
            break

        if result.get(
            "state"
        ) in {
            "campaign_disabled",
            "paused",
            "shutdown_requested",
        }:
            print(
                "Campaign runtime entered "
                f"state: {result.get('state')}"
            )
            break

        if (
            maximum_cycles > 0
            and completed_cycles
            >= maximum_cycles
        ):
            print(
                "Maximum cycle limit reached."
            )
            break

        print(
            f"Sleeping "
            f"{interval_seconds} seconds "
            "before the next cycle.",
            flush=True,
        )

        time.sleep(
            interval_seconds
        )

    final_status = state.load()

    final_status.update(
        {
            "running": False,
            "pid": None,
            "automatic_execution": False,
            "order_submission": False,
        }
    )

    state.save(
        final_status
    )

    print()
    print(
        "Segment 4B-4 foreground "
        "campaign stopped."
    )
    print(
        f"Completed cycles: "
        f"{completed_cycles}"
    )
    print(
        "Orders submitted: 0"
    )


if __name__ == "__main__":
    main()
