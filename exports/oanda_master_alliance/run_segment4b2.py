from __future__ import annotations

import argparse
import json
import time

from segment4b2.common import (
    load_config,
)
from segment4b2.engine import (
    ShadowTradingEngine,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--daemon",
        action="store_true",
    )

    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
    )

    arguments = parser.parse_args()

    config = load_config()

    assert config.get(
        "research_only"
    ) is True

    assert config.get(
        "practice_only"
    ) is True

    assert config.get(
        "live_environment_allowed"
    ) is False

    assert config.get(
        "automatic_execution"
    ) is False

    assert config.get(
        (
            "practice_order_"
            "submission_enabled"
        )
    ) is False

    engine = ShadowTradingEngine(
        config
    )

    while True:
        result = engine.run()

        print(
            json.dumps(
                result.to_dict(),
                indent=2,
            )
        )

        print()
        print(
            "Shadow cycle completed."
        )
        print(
            "Automatic execution: false"
        )
        print(
            "Orders submitted: 0"
        )

        if not arguments.daemon:
            break

        time.sleep(
            max(
                30,
                arguments.interval_seconds,
            )
        )


if __name__ == "__main__":
    main()
