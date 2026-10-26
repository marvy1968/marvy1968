from __future__ import annotations

import hashlib
from typing import Any

from segment4b.common import (
    append_jsonl,
    atomic_write_json,
    load_segment4b_config,
    storage_path,
    utc_now_iso,
)
from segment4b.duplicate_registry import (
    ExecutionDuplicateRegistry,
)
from segment4b.models import (
    OrderIntent,
    OrderReceipt,
)


class SimulatedPracticeRouter:
    """
    Simulates OANDA order acceptance.

    This class has no OANDA client and cannot make HTTP requests.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_segment4b_config()
        )

        self.registry = (
            ExecutionDuplicateRegistry(
                self.config
            )
        )

    @staticmethod
    def _identifier(
        prefix: str,
        intent_id: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                prefix
                + "|"
                + intent_id
            ).encode(
                "utf-8"
            )
        ).hexdigest()[:16]

        return (
            prefix
            + "-"
            + digest
        )

    def simulate(
        self,
        *,
        intent: OrderIntent,
        payload: dict[str, Any],
    ) -> OrderReceipt:
        intent_key = self.registry.fingerprint(
            intent.intent_id,
            intent.instrument,
            intent.side,
            intent.authorization_id,
        )

        authorization_key = (
            self.registry.fingerprint(
                intent.authorization_id,
                intent.instrument,
                intent.side,
            )
        )

        if self.registry.exists(
            "order_intents",
            intent_key,
        ):
            raise ValueError(
                "duplicate_order_intent"
            )

        if self.registry.exists(
            "authorizations",
            authorization_key,
        ):
            raise ValueError(
                "duplicate_execution_authorization"
            )

        if not self.registry.register(
            "order_intents",
            intent_key,
            {
                "intent_id": (
                    intent.intent_id
                ),
                "instrument": (
                    intent.instrument
                ),
                "side": intent.side,
                "mode": "simulation_only",
            },
        ):
            raise ValueError(
                "duplicate_order_intent"
            )

        if not self.registry.register(
            "authorizations",
            authorization_key,
            {
                "authorization_id": (
                    intent.authorization_id
                ),
                "instrument": (
                    intent.instrument
                ),
                "side": intent.side,
                "mode": "simulation_only",
            },
        ):
            raise ValueError(
                "duplicate_execution_authorization"
            )

        broker_order_id = self._identifier(
            "sim-order",
            intent.intent_id,
        )

        broker_trade_id = self._identifier(
            "sim-trade",
            intent.intent_id,
        )

        transaction_id = self._identifier(
            "sim-tx",
            intent.intent_id,
        )

        receipt = OrderReceipt(
            timestamp=utc_now_iso(),
            intent_id=intent.intent_id,
            submitted=False,
            accepted=True,
            instrument=intent.instrument,
            side=intent.side,
            signed_units=(
                intent.signed_units
            ),
            broker_order_id=(
                broker_order_id
            ),
            broker_trade_id=(
                broker_trade_id
            ),
            transaction_id=(
                transaction_id
            ),
            status="simulated_acceptance",
            message=(
                "Order lifecycle simulated. "
                "No OANDA request was submitted."
            ),
            raw_response={
                "simulation": True,
                "submitted": False,
                "payload": payload,
                "orderCreateTransaction": {
                    "id": transaction_id,
                    "instrument": (
                        intent.instrument
                    ),
                    "units": str(
                        intent.signed_units
                    ),
                    "type": "MARKET_ORDER",
                },
                "orderFillTransaction": {
                    "id": transaction_id,
                    "orderID": (
                        broker_order_id
                    ),
                    "tradeOpened": {
                        "tradeID": (
                            broker_trade_id
                        ),
                        "units": str(
                            intent.signed_units
                        ),
                        "price": str(
                            intent.entry_price
                        ),
                    },
                },
            },
            automatic_execution=False,
            order_submission=False,
        )

        receipt_path = (
            storage_path(
                self.config,
                "execution_status",
            ).parent
            / "receipts"
            / (
                f"{intent.intent_id}.json"
            )
        )

        atomic_write_json(
            receipt_path,
            receipt.to_dict(),
        )

        append_jsonl(
            storage_path(
                self.config,
                "receipt_history",
            ),
            receipt.to_dict(),
        )

        return receipt
