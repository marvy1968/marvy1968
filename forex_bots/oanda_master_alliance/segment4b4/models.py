from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CandidateDecision:
    instrument: str
    side: str
    alliance_score: float
    confirming_experts: int
    reward_risk: float
    entry_price: float
    stop_price: float
    target_price: float
    units: int
    authorization_id: str
    source_timestamp: str
    expert_names: list[str] = field(
        default_factory=list
    )
    setup_names: list[str] = field(
        default_factory=list
    )
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateLoadResult:
    timestamp: str
    source_exists: bool
    loaded: int
    approved: int
    rejected: int
    candidates: list[CandidateDecision]
    rejection_reasons: list[dict[str, Any]]
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [
            candidate.to_dict()
            for candidate in self.candidates
        ]
        return payload


@dataclass(slots=True)
class CampaignCycleResult:
    timestamp: str
    cycle_number: int
    successful: bool
    candidates_loaded: int
    candidates_approved: int
    shadow_trades_opened: int
    open_trades_checked: int
    shadow_trades_closed: int
    total_open_trades: int
    total_closed_trades: int
    checkpoint: str
    target_closed_trades: int
    campaign_complete: bool
    errors: list[str] = field(
        default_factory=list
    )
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
