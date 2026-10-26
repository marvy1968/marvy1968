from __future__ import annotations

import argparse
import json

from segment4b4.checkpoint_alerts import (
    CheckpointAlertManager,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deliver locally tracked "
            "Segment 4B-4 checkpoint alerts."
        )
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Deliver the latest alert even "
            "if it was previously delivered."
        ),
    )

    arguments = parser.parse_args()

    result = (
        CheckpointAlertManager()
        .deliver(
            force=arguments.force
        )
    )

    print(
        json.dumps(
            result.to_dict(),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
