from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ShadowCandidate:
    timestamp: str
    candidate_id: str
    instrument: str
    side: str
    alliance_score: float
    confirming_experts: int
    reward_risk: float
    entry_price: float
    stop_price: float
    target_price: float
    authorization_id: str
    source_timestamp: str
    risk_pct: float
    units: int
    experts: list[str] = field(
        default_factory=list
    )
    setups: list[str] = field(
        default_factory=list
    )
    regime: str = ""
    session: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class ShadowTrade:
    shadow_trade_id: str
    candidate_id: str
    opened_at: str
    status: str
    instrument: str
    side: str
    alliance_score: float
    confirming_experts: int
    reward_risk: float
    authorization_id: str
    source_timestamp: str
    entry_price: float
    stop_price: float
    target_price: float
    initial_risk_distance: float
    pip_size: float
    risk_pct: float
    units: int
    experts: list[str] = field(
        default_factory=list
    )
    setups: list[str] = field(
        default_factory=list
    )
    regime: str = ""
    session: str = ""
    current_price: float = 0.0
    unrealized_r: float = 0.0
    maximum_favorable_excursion_r: float = 0.0
    maximum_adverse_excursion_r: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    last_checked_at: str = ""
    close_price: float = 0.0
    closed_at: str = ""
    outcome: str = ""
    exit_reason: str = ""
    realized_r: float = 0.0
    bars_held: int = 0
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class ShadowCycleResult:
    timestamp: str
    successful: bool
    candidates_loaded: int
    candidates_approved: int
    trades_opened: int
    open_trades_checked: int
    trades_closed: int
    open_trade_count: int
    closed_trade_count: int
    errors: list[str] = field(
        default_factory=list
    )
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )
