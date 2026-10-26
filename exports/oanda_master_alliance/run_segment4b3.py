from __future__ import annotations

import json

from segment4b3.common import (
    load_config,
)
from segment4b3.engine import (
    ReadinessEngine,
)


def main() -> None:
    config = load_config()

    assert config[
        "research_only"
    ] is True

    assert config[
        "practice_only"
    ] is True

    assert config[
        "live_environment_allowed"
    ] is False

    assert config[
        "automatic_execution"
    ] is False

    assert config[
        (
            "practice_order_"
            "submission_enabled"
        )
    ] is False

    assert config[
        (
            "actual_order_"
            "submission_enabled"
        )
    ] is False

    result = ReadinessEngine(
        config
    ).run()

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4B-3 readiness analysis completed."
    )
    print(
        "Automatic promotion: false"
    )
    print(
        "Automatic expert-weight changes: false"
    )
    print(
        "Automatic execution: false"
    )
    print(
        "Orders submitted: 0"
    )


if __name__ == "__main__":
    main()
