from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from segment4b.common import (
    append_jsonl,
    atomic_write_json,
    load_segment4b_config,
    storage_path,
    utc_now_iso,
)
from segment4b.models import (
    OrderIntent,
    OrderReceipt,
)


@dataclass
class FillVerificationResult:
    timestamp: str
    passed: bool
    intent_id: str
    instrument: str
    side: str
    expected_units: int
    filled_units: int
    trade_opened: bool
    stop_attached: bool
    target_attached: bool
    partial_fill: bool
    reasons: list[str]
    warnings: list[str]
    mode: str
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


class SimulatedFillVerifier:
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

        fill_config = self.config.get(
            "fill_verification",
            {},
        )

        self.fill_config = (
            fill_config
            if isinstance(
                fill_config,
                dict,
            )
            else {}
        )

    def verify(
        self,
        *,
        intent: OrderIntent,
        receipt: OrderReceipt,
        payload: dict[str, Any],
    ) -> FillVerificationResult:
        reasons: list[str] = []
        warnings: list[str] = []

        raw = receipt.raw_response

        fill_transaction = raw.get(
            "orderFillTransaction",
            {},
        )

        if not isinstance(
            fill_transaction,
            dict,
        ):
            fill_transaction = {}

        trade_opened_payload = (
            fill_transaction.get(
                "tradeOpened",
                {},
            )
        )

        trade_opened = isinstance(
            trade_opened_payload,
            dict,
        ) and bool(
            trade_opened_payload.get(
                "tradeID"
            )
        )

        try:
            filled_units = abs(
                int(
                    float(
                        trade_opened_payload.get(
                            "units",
                            0,
                        )
                    )
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            filled_units = 0

        expected_units = abs(
            int(
                intent.units
            )
        )

        partial_fill = (
            filled_units
            not in {
                0,
                expected_units,
            }
        )

        order_payload = payload.get(
            "order",
            {},
        )

        if not isinstance(
            order_payload,
            dict,
        ):
            order_payload = {}

        stop_attached = isinstance(
            order_payload.get(
                "stopLossOnFill"
            ),
            dict,
        ) and bool(
            order_payload[
                "stopLossOnFill"
            ].get(
                "price"
            )
        )

        target_attached = isinstance(
            order_payload.get(
                "takeProfitOnFill"
            ),
            dict,
        ) and bool(
            order_payload[
                "takeProfitOnFill"
            ].get(
                "price"
            )
        )

        if bool(
            self.fill_config.get(
                "require_trade_opened",
                True,
            )
        ) and not trade_opened:
            reasons.append(
                "trade_not_opened"
            )

        if filled_units != expected_units:
            reasons.append(
                "filled_units_mismatch"
            )

        if bool(
            self.fill_config.get(
                "require_stop_attached",
                True,
            )
        ) and not stop_attached:
            reasons.append(
                "stop_not_attached"
            )

        if bool(
            self.fill_config.get(
                "require_target_attached",
                True,
            )
        ) and not target_attached:
            reasons.append(
                "target_not_attached"
            )

        if (
            bool(
                self.fill_config.get(
                    "reject_partial_fill",
                    True,
                )
            )
            and partial_fill
        ):
            reasons.append(
                "partial_fill_rejected"
            )

        result = FillVerificationResult(
            timestamp=utc_now_iso(),
            passed=(
                len(
                    reasons
                )
                == 0
            ),
            intent_id=(
                intent.intent_id
            ),
            instrument=(
                intent.instrument
            ),
            side=intent.side,
            expected_units=(
                expected_units
            ),
            filled_units=(
                filled_units
            ),
            trade_opened=(
                trade_opened
            ),
            stop_attached=(
                stop_attached
            ),
            target_attached=(
                target_attached
            ),
            partial_fill=(
                partial_fill
            ),
            reasons=reasons,
            warnings=warnings,
            mode="simulation_only",
            automatic_execution=False,
            order_submission=False,
        )

        fill_path = (
            storage_path(
                self.config,
                "execution_status",
            ).parent
            / "fills"
            / (
                f"{intent.intent_id}.json"
            )
        )

        atomic_write_json(
            fill_path,
            result.to_dict(),
        )

        append_jsonl(
            storage_path(
                self.config,
                "fill_history",
            ),
            result.to_dict(),
        )

        return result
