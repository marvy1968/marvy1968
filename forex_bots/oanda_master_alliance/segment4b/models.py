from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ReconciliationIssue:
    code: str
    severity: str
    message: str
    blocking: bool
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
class BrokerReconciliation:
    timestamp: str
    passed: bool
    environment: str
    account_id: str
    open_positions: int
    open_trades: int
    pending_orders: int
    known_positions: int
    known_orders: int
    issues: list[
        ReconciliationIssue
    ]
    broker_positions: list[
        dict[str, Any]
    ]
    broker_trades: list[
        dict[str, Any]
    ]
    broker_orders: list[
        dict[str, Any]
    ]
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(
        self,
    ) -> dict[str, Any]:
        payload = asdict(
            self
        )

        payload[
            "issues"
        ] = [
            issue.to_dict()
            for issue in self.issues
        ]

        return payload


@dataclass
class ExecutionGateDecision:
    timestamp: str
    allowed: bool
    mode: str
    reasons: list[str]
    warnings: list[str]
    metadata: dict[str, Any]
    automatic_execution: bool
    practice_order_submission_enabled: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass
class OrderIntent:
    timestamp: str
    intent_id: str
    instrument: str
    side: str
    units: int
    signed_units: int
    order_type: str
    time_in_force: str
    position_fill: str
    entry_price: float
    stop_price: float
    target_price: float
    reward_risk: float
    alliance_score: float
    confirming_experts: int
    authorization_id: str
    client_extension_id: str
    source_timestamp: str
    mode: str
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
class OrderReceipt:
    timestamp: str
    intent_id: str
    submitted: bool
    accepted: bool
    instrument: str
    side: str
    signed_units: int
    broker_order_id: str
    broker_trade_id: str
    transaction_id: str
    status: str
    message: str
    raw_response: dict[str, Any]
    automatic_execution: bool
    order_submission: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )
