from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ExpertSignal:
    expert: str
    instrument: str
    side: str
    score: float
    status: str
    setup: str
    regime: str
    session: str
    entry_style: str
    entry_price: float
    stop_price: float
    target_price: float
    reward_risk: float
    confidence: float
    reasons: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AllianceDecision:
    instrument: str
    side: str
    decision: str
    alliance_score: float
    confidence: float
    confirming_experts: int
    opposing_experts: int
    regime: str
    session: str
    entry_price: float
    stop_price: float
    target_price: float
    reward_risk: float
    expert_signals: list[dict[str, Any]] = field(
        default_factory=list
    )
    reasons: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(
        default_factory=list
    )
    risk_notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
