from __future__ import annotations

import hashlib
from typing import Any

from segment4b.common import (
    append_jsonl,
    load_segment4b_config,
    storage_path,
    utc_now_iso,
)
from segment4b.duplicate_registry import (
    ExecutionDuplicateRegistry,
)
from segment4b.models import (
    OrderIntent,
)


class OrderIntentBuilder:
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
    def _intent_id(
        *,
        instrument: str,
        side: str,
        authorization_id: str,
        source_timestamp: str,
    ) -> str:
        material = "|".join(
            [
                instrument,
                side,
                authorization_id,
                source_timestamp,
            ]
        )

        return hashlib.sha256(
            material.encode(
                "utf-8"
            )
        ).hexdigest()[:20]

    def build(
        self,
        record: dict[str, Any],
    ) -> OrderIntent:
        risk = record.get(
            "risk",
            {},
        )

        authorization = record.get(
            "authorization",
            {},
        )

        position_size = (
            risk.get(
                "position_size",
                {},
            )
            if isinstance(
                risk,
                dict,
            )
            else {}
        )

        instrument = str(
            record.get(
                "instrument",
                "",
            )
        ).strip().upper()

        side = str(
            record.get(
                "side",
                "",
            )
        ).strip().lower()

        if side not in {
            "buy",
            "sell",
        }:
            raise ValueError(
                f"Invalid order side: {side}"
            )

        authorization_id = str(
            authorization.get(
                "authorization_id",
                "",
            )
        ).strip()

        if not authorization_id:
            raise ValueError(
                "Missing Segment 3 authorization ID."
            )

        source_timestamp = str(
            authorization.get(
                "timestamp",
                record.get(
                    "timestamp",
                    "",
                ),
            )
        )

        intent_id = self._intent_id(
            instrument=instrument,
            side=side,
            authorization_id=(
                authorization_id
            ),
            source_timestamp=(
                source_timestamp
            ),
        )

        units = abs(
            int(
                position_size.get(
                    "units",
                    0,
                )
            )
        )

        signed_units = (
            units
            if side == "buy"
            else -units
        )

        entry_price = float(
            position_size.get(
                "entry_price",
                record.get(
                    "entry_price",
                    0.0,
                ),
            )
        )

        stop_price = float(
            position_size.get(
                "stop_price",
                record.get(
                    "stop_price",
                    0.0,
                ),
            )
        )

        target_price = float(
            record.get(
                "target_price",
                0.0,
            )
        )

        if units <= 0:
            raise ValueError(
                "Order units must be positive."
            )

        if (
            entry_price <= 0
            or stop_price <= 0
            or target_price <= 0
        ):
            raise ValueError(
                "Entry, stop and target prices are required."
            )

        order_policy = self.config.get(
            "order_policy",
            {},
        )

        if not isinstance(
            order_policy,
            dict,
        ):
            order_policy = {}

        intent = OrderIntent(
            timestamp=utc_now_iso(),
            intent_id=intent_id,
            instrument=instrument,
            side=side,
            units=units,
            signed_units=signed_units,
            order_type=str(
                order_policy.get(
                    "order_type",
                    "MARKET",
                )
            ),
            time_in_force=str(
                order_policy.get(
                    "time_in_force",
                    "FOK",
                )
            ),
            position_fill=str(
                order_policy.get(
                    "position_fill",
                    "DEFAULT",
                )
            ),
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            reward_risk=float(
                record.get(
                    "reward_risk",
                    0.0,
                )
            ),
            alliance_score=float(
                record.get(
                    "alliance_score",
                    0.0,
                )
            ),
            confirming_experts=int(
                record.get(
                    "confirming_experts",
                    0,
                )
            ),
            authorization_id=(
                authorization_id
            ),
            client_extension_id=(
                f"ma4b-{intent_id}"
            ),
            source_timestamp=(
                source_timestamp
            ),
            mode="research_preview",
            metadata={
                "risk_pct": (
                    risk.get(
                        "risk_pct"
                    )
                    if isinstance(
                        risk,
                        dict,
                    )
                    else None
                ),
                "segment3_authorized": bool(
                    authorization.get(
                        "authorized",
                        False,
                    )
                ),
            },
        )

        append_jsonl(
            storage_path(
                self.config,
                "intent_history",
            ),
            intent.to_dict(),
        )

        return intent
