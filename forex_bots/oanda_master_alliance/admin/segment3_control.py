from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )

from core.common import ROOT


KILL_SWITCH = (
    ROOT / "state/SEGMENT3_KILL_SWITCH"
)

STATUS_FILE = (
    ROOT
    / "state/execution/"
    "segment3_status.json"
)

PAUSE_FILE = (
    ROOT
    / "state/execution/"
    "segment3_pause.json"
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "status",
            "kill",
            "unkill",
            "pause-status",
            "clear-pause",
        ],
    )

    arguments = parser.parse_args()

    if arguments.command == "kill":
        KILL_SWITCH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        KILL_SWITCH.write_text(
            "Segment 3 kill switch enabled.\n",
            encoding="utf-8",
        )

        print(
            "Segment 3 kill switch enabled."
        )

    elif arguments.command == "unkill":
        KILL_SWITCH.unlink(
            missing_ok=True
        )

        print(
            "Segment 3 kill switch disabled."
        )

    elif arguments.command == "clear-pause":
        PAUSE_FILE.unlink(
            missing_ok=True
        )

        print(
            "Segment 3 risk pause cleared."
        )

    elif arguments.command == "pause-status":
        if not PAUSE_FILE.exists():
            print(
                "No Segment 3 risk pause."
            )
        else:
            print(
                PAUSE_FILE.read_text(
                    encoding="utf-8"
                )
            )

    elif arguments.command == "status":
        if not STATUS_FILE.exists():
            print(
                json.dumps(
                    {
                        "segment3_has_run": (
                            False
                        ),
                        "kill_switch": (
                            KILL_SWITCH.exists()
                        ),
                        "paused": (
                            PAUSE_FILE.exists()
                        ),
                    },
                    indent=2,
                )
            )
            return

        payload = json.loads(
            STATUS_FILE.read_text(
                encoding="utf-8"
            )
        )

        summary = {
            "segment3_has_run": True,
            "timestamp": payload.get(
                "timestamp"
            ),
            "environment": payload.get(
                "environment"
            ),
            "practice_only": payload.get(
                "practice_only"
            ),
            "automatic_execution": (
                payload.get(
                    "automatic_execution"
                )
            ),
            "submission_enabled": (
                payload.get(
                    "practice_order_submission_enabled"
                )
            ),
            "submit_requested": (
                payload.get(
                    "submit_requested"
                )
            ),
            "evaluated_candidates": (
                payload.get(
                    "evaluated_candidates"
                )
            ),
            "risk_approved_candidates": (
                payload.get(
                    "risk_approved_candidates"
                )
            ),
            "submitted_orders": (
                payload.get(
                    "submitted_orders"
                )
            ),
            "kill_switch": (
                KILL_SWITCH.exists()
            ),
            "paused": PAUSE_FILE.exists(),
        }

        print(
            json.dumps(
                summary,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
