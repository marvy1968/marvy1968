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
            "reconciliation",
            "summary",
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

    elif arguments.command == "reconciliation":
        payload = read_json(
            storage_path(
                config,
                "latest_reconciliation",
            ),
            default={},
        )

        print(
            json.dumps(
                payload,
                indent=2,
            )
        )

    elif arguments.command == "summary":
        payload = read_json(
            storage_path(
                config,
                "execution_status",
            ),
            default={},
        )

        reconciliation = payload.get(
            "reconciliation",
            {},
        )

        print()
        print(
            "OANDA MASTER AI — SEGMENT 4B-1"
        )
        print("=" * 80)

        print(
            "Environment:             "
            f"{payload.get('environment', 'unknown')}"
        )

        print(
            "Reconciliation passed:   "
            f"{reconciliation.get('passed')}"
        )

        print(
            "Segment 3 candidates:    "
            f"{payload.get('segment3_candidates', 0)}"
        )

        print(
            "Validated candidates:    "
            f"{payload.get('validated_candidates', 0)}"
        )

        print(
            "Dry-run intents:         "
            f"{payload.get('dry_run_intents_created', 0)}"
        )

        print(
            "Execution gate allowed:  "
            f"{payload.get('execution_gate_allowed', False)}"
        )

        print(
            "Automatic execution:     "
            f"{payload.get('automatic_execution', False)}"
        )

        print(
            "Orders submitted:        "
            f"{payload.get('orders_submitted', 0)}"
        )


if __name__ == "__main__":
    main()
