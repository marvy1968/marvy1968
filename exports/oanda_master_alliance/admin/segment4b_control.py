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

if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


from core.common import ROOT
from segment4b.activation_gate import (
    PracticeExecutionGate,
)
from segment4b.common import (
    load_segment4b_config,
    read_json,
    storage_path,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "status",
            "gate",
            "counters",
            "clear-simulation-counters",
            "clear-simulation-duplicates",
        ],
    )

    arguments = parser.parse_args()

    config = load_segment4b_config()

    if arguments.command == "status":
        payload = read_json(
            storage_path(
                config,
                "execution_status",
            ),
            default={},
        )

        print(
            json.dumps(
                payload,
                indent=2,
            )
        )

    elif arguments.command == "gate":
        gate = PracticeExecutionGate(
            config
        ).evaluate()

        print(
            json.dumps(
                gate.to_dict(),
                indent=2,
            )
        )

    elif arguments.command == "counters":
        hourly = read_json(
            storage_path(
                config,
                "hourly_counter",
            ),
            default={},
        )

        daily = read_json(
            storage_path(
                config,
                "daily_counter",
            ),
            default={},
        )

        print(
            json.dumps(
                {
                    "hourly": hourly,
                    "daily": daily,
                    "automatic_execution": False,
                    "order_submission": False,
                },
                indent=2,
            )
        )

    elif (
        arguments.command
        == "clear-simulation-counters"
    ):
        storage_path(
            config,
            "hourly_counter",
        ).unlink(
            missing_ok=True
        )

        storage_path(
            config,
            "daily_counter",
        ).unlink(
            missing_ok=True
        )

        print(
            "Simulation counters cleared."
        )

    elif (
        arguments.command
        == "clear-simulation-duplicates"
    ):
        registry_path = storage_path(
            config,
            "duplicate_registry",
        )

        payload = read_json(
            registry_path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        payload[
            "authorizations"
        ] = {}

        payload[
            "order_intents"
        ] = {}

        payload[
            "broker_orders"
        ] = {}

        from segment4b.common import (
            atomic_write_json,
            utc_now_iso,
        )

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            registry_path,
            payload,
        )

        print(
            "Simulation execution duplicates cleared."
        )
        print(
            "Signal duplicate history was preserved."
        )


if __name__ == "__main__":
    main()
