from __future__ import annotations

from uuid import uuid4

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


from segment4b.activation_gate import (
    PracticeExecutionGate,
)
from segment4b.common import (
    load_segment4b_config,
)
from segment4b.duplicate_registry import (
    ExecutionDuplicateRegistry,
)


def main() -> None:
    config = load_segment4b_config()

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

    gate = PracticeExecutionGate(
        config
    ).evaluate()

    assert gate.allowed is False

    assert (
        "automatic_execution_disabled"
        in gate.reasons
    )

    assert (
        "practice_order_submission_disabled"
        in gate.reasons
    )

    registry = (
        ExecutionDuplicateRegistry(
            config
        )
    )

    verification_nonce = uuid4().hex

    key = registry.fingerprint(
        "verification",
        verification_nonce,
        "EUR_USD",
        "buy",
    )

    first = registry.register(
        "signals",
        key,
        {
            "verification": True,
        },
    )

    second = registry.register(
        "signals",
        key,
        {
            "verification": True,
        },
    )

    assert first is True
    assert second is False

    required_files = [
        "config/segment4b.yaml",
        "segment4b/common.py",
        "segment4b/models.py",
        "segment4b/activation_gate.py",
        "segment4b/duplicate_registry.py",
        "segment4b/order_intent.py",
    ]

    missing = [
        file_name
        for file_name in required_files
        if not (
            ROOT
            / file_name
        ).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4B-1 files: "
            + ", ".join(
                missing
            )
        )

    result = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "execution_gate_allowed": (
            gate.allowed
        ),
        "execution_gate_reasons": (
            gate.reasons
        ),
        "duplicate_first_registration": (
            first
        ),
        "duplicate_second_registration": (
            second
        ),
        "practice_only": (
            config[
                "practice_only"
            ]
        ),
        "automatic_execution": (
            config[
                "automatic_execution"
            ]
        ),
        "order_submission": (
            config[
                (
                    "practice_order_"
                    "submission_enabled"
                )
            ]
        ),
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-1 PART 1"
    )
    print("=" * 72)

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4B-1 Part 1 "
        "verification passed."
    )


if __name__ == "__main__":
    main()
