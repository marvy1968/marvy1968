from __future__ import annotations

from brain.alliance_engine import (
    AllianceEngine,
)
from experts.registry import (
    build_experts,
)


def test_expert_registry() -> None:
    engine = AllianceEngine()

    experts = build_experts(
        engine.config
    )

    assert len(experts) == 7


def test_execution_disabled() -> None:
    engine = AllianceEngine()

    assert (
        engine.segment[
            "automatic_execution"
        ]
        is False
    )


def test_research_only() -> None:
    engine = AllianceEngine()

    assert (
        engine.segment[
            "research_only"
        ]
        is True
    )
