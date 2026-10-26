from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


EvidenceLevel = Literal[
    "primary",
    "secondary",
    "community",
    "inference",
    "unverified",
]

RuleType = Literal[
    "market_selection",
    "regime",
    "entry",
    "confirmation",
    "stop",
    "target",
    "management",
    "risk",
    "avoidance",
]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    expert: str
    title: str
    source_type: str
    creator_or_publisher: str
    reference: str
    publication_year: int | None = None
    evidence_level: EvidenceLevel = "secondary"
    reviewed: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoctrineRule:
    rule_id: str
    expert: str
    rule_type: RuleType
    statement: str
    source_ids: tuple[str, ...]
    confidence: float
    forex_adaptation: str = ""
    mechanical: bool = False
    implementation_status: str = "research"
    invalidation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategySpecification:
    strategy_id: str
    expert: str
    name: str
    description: str
    preferred_regimes: tuple[str, ...]
    preferred_sessions: tuple[str, ...]
    preferred_timeframes: tuple[str, ...]
    required_features: tuple[str, ...]
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    risk_rules: tuple[str, ...]
    disqualifiers: tuple[str, ...]
    status: str = "research_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExpertProfile:
    expert_id: str
    display_name: str
    philosophy: str
    edge_hypothesis: str
    strengths: list[str]
    weaknesses: list[str]
    compatible_regimes: list[str]
    incompatible_regimes: list[str]
    sources: list[SourceRecord] = field(
        default_factory=list
    )
    doctrine: list[DoctrineRule] = field(
        default_factory=list
    )
    strategies: list[StrategySpecification] = field(
        default_factory=list
    )
    implementation_notes: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "expert_id": self.expert_id,
            "display_name": self.display_name,
            "philosophy": self.philosophy,
            "edge_hypothesis": self.edge_hypothesis,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "compatible_regimes": (
                self.compatible_regimes
            ),
            "incompatible_regimes": (
                self.incompatible_regimes
            ),
            "sources": [
                item.to_dict()
                for item in self.sources
            ],
            "doctrine": [
                item.to_dict()
                for item in self.doctrine
            ],
            "strategies": [
                item.to_dict()
                for item in self.strategies
            ],
            "implementation_notes": (
                self.implementation_notes
            ),
        }
