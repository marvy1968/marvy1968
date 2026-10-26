from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    atomic_write_json,
    load_segment4b4_config,
    read_json,
    utc_now_iso,
)


def load_control(
    state: CampaignState,
) -> dict:
    payload = read_json(
        state.control_path,
        default={},
    )

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    return payload


def save_control(
    state: CampaignState,
    payload: dict,
) -> None:
    payload.update(
        {
            "updated_at": utc_now_iso(),
            "automatic_promotion": False,
            "automatic_execution": False,
            "order_submission": False,
        }
    )

    atomic_write_json(
        state.control_path,
        payload,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Control the research-only "
            "Segment 4B-4 shadow campaign."
        )
    )

    parser.add_argument(
        "command",
        choices=[
            "status",
            "pause",
            "resume",
            "stop",
            "clear-stop",
            "enable",
            "disable",
        ],
    )

    parser.add_argument(
        "--reason",
        default="manual_review",
    )

    arguments = parser.parse_args()

    config = load_segment4b4_config()

    state = CampaignState(
        config
    )

    if arguments.command == "status":
        result = {
            "status": state.load(),
            "control": load_control(
                state
            ),
            "paused": state.paused(),
            "shutdown_requested": (
                state.shutdown_requested()
            ),
            "lock_present": (
                state.lock_path.exists()
            ),
            "pid_present": (
                state.pid_path.exists()
            ),
            "automatic_execution": False,
            "order_submission": False,
        }

        print(
            json.dumps(
                result,
                indent=2,
            )
        )

        return

    if arguments.command == "pause":
        state.pause(
            arguments.reason
        )

        print(
            "Shadow campaign paused."
        )

        print(
            f"Reason: {arguments.reason}"
        )

        return

    if arguments.command == "resume":
        state.resume()

        control = load_control(
            state
        )

        control[
            "manual_pause"
        ] = False

        save_control(
            state,
            control,
        )

        print(
            "Shadow campaign resumed."
        )

        return

    if arguments.command == "stop":
        state.request_shutdown()

        print(
            "Shadow campaign graceful "
            "shutdown requested."
        )

        return

    if arguments.command == "clear-stop":
        state.clear_shutdown()

        print(
            "Shadow campaign shutdown "
            "request cleared."
        )

        return

    control = load_control(
        state
    )

    if arguments.command == "enable":
        control[
            "campaign_enabled"
        ] = True

        save_control(
            state,
            control,
        )

        print(
            "Shadow campaign enabled."
        )

        print(
            "Automatic execution remains disabled."
        )

        return

    if arguments.command == "disable":
        control[
            "campaign_enabled"
        ] = False

        save_control(
            state,
            control,
        )

        print(
            "Shadow campaign disabled."
        )

        print(
            "Automatic execution remains disabled."
        )

        return


if __name__ == "__main__":
    main()
