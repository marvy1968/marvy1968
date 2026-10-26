from __future__ import annotations

from decimal import Decimal
from typing import Any

from segment4b.models import (
    OrderIntent,
)


def price_precision(
    instrument: str,
) -> int:
    return (
        3
        if instrument.endswith(
            "_JPY"
        )
        else 5
    )


def format_price(
    instrument: str,
    value: float,
) -> str:
    precision = price_precision(
        instrument
    )

    return (
        f"{Decimal(str(value)):.{precision}f}"
    )


class DryRunPayloadBuilder:
    """
    Produces an OANDA-compatible market order request body.

    This class does not possess a broker client and cannot submit the payload.
    """

    @staticmethod
    def build(
        intent: OrderIntent,
    ) -> dict[str, Any]:
        if intent.order_type.upper() != "MARKET":
            raise ValueError(
                "Segment 4B-1 Part 2 supports MARKET dry-runs only."
            )

        order = {
            "type": "MARKET",
            "instrument": (
                intent.instrument
            ),
            "units": str(
                intent.signed_units
            ),
            "timeInForce": (
                intent.time_in_force.upper()
            ),
            "positionFill": (
                intent.position_fill.upper()
            ),
            "clientExtensions": {
                "id": (
                    intent.client_extension_id
                ),
                "tag": (
                    "oanda_master_ai_4b"
                ),
                "comment": (
                    "controlled_practice_execution"
                ),
            },
            "stopLossOnFill": {
                "price": format_price(
                    intent.instrument,
                    intent.stop_price,
                ),
                "timeInForce": "GTC",
            },
            "takeProfitOnFill": {
                "price": format_price(
                    intent.instrument,
                    intent.target_price,
                ),
                "timeInForce": "GTC",
            },
        }

        return {
            "order": order
        }
