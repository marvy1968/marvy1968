from __future__ import annotations

from uuid import uuid4

from segment4b.activation_gate import (
    PracticeExecutionGate,
)
from segment4b.common import (
    load_segment4b_config,
)
from segment4b.duplicate_registry import (
    ExecutionDuplicateRegistry,
)


def test_execution_starts_blocked() -> None:
    config = load_segment4b_config()

    result = PracticeExecutionGate(
        config
    ).evaluate()

    assert result.allowed is False


def test_duplicate_registry() -> None:
    config = load_segment4b_config()

    registry = (
        ExecutionDuplicateRegistry(
            config
        )
    )

    verification_nonce = uuid4().hex

    key = registry.fingerprint(
        "pytest",
        verification_nonce,
        "GBP_USD",
        "sell",
    )

    registry.register(
        "signals",
        key,
    )

    assert registry.exists(
        "signals",
        key,
    )
