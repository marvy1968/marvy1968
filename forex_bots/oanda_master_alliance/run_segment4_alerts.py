from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.alert_manager import (
    AlertManager,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI operational alerts"
        )
    )

    parser.add_argument(
        "--test",
        action="store_true",
    )

    parser.add_argument(
        "--severity",
        choices=[
            "info",
            "warning",
            "error",
            "critical",
        ],
        default="warning",
    )

    parser.add_argument(
        "--force",
        action="store_true",
    )

    arguments = parser.parse_args()

    manager = AlertManager()

    if arguments.test:
        result = manager.send(
            event_type="manual_test",
            severity=arguments.severity,
            title=(
                "OANDA Master AI alert test"
            ),
            message=(
                "Segment 4A-3 operational "
                "alert delivery test."
            ),
            metadata={
                "test": True,
            },
            force=arguments.force,
        )

        print(
            json.dumps(
                result.to_dict(),
                indent=2,
            )
        )

        return

    results = manager.evaluate_operations()

    print(
        json.dumps(
            {
                "alerts_emitted": len(
                    results
                ),
                "results": [
                    result.to_dict()
                    for result in results
                ],
                "automatic_execution": False,
                "order_submission": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
