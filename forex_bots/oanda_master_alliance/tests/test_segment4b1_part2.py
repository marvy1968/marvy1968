from __future__ import annotations

from datetime import datetime, timezone

from segment4b.models import (
    OrderIntent,
)
from segment4b.payload_builder import (
    DryRunPayloadBuilder,
)


def test_sell_payload() -> None:
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    intent = OrderIntent(
        timestamp=timestamp,
        intent_id="test-sell",
        instrument="USD_CHF",
        side="sell",
        units=1000,
        signed_units=-1000,
        order_type="MARKET",
        time_in_force="FOK",
        position_fill="DEFAULT",
        entry_price=0.80500,
        stop_price=0.80600,
        target_price=0.80300,
        reward_risk=2.0,
        alliance_score=85.0,
        confirming_experts=3,
        authorization_id="test-auth",
        client_extension_id="test-client",
        source_timestamp=timestamp,
        mode="research_preview",
    )

    payload = DryRunPayloadBuilder.build(
        intent
    )

    order = payload[
        "order"
    ]

    assert order[
        "units"
    ] == "-1000"

    assert (
        order[
            "stopLossOnFill"
        ][
            "price"
        ]
        == "0.80600"
    )

    assert (
        order[
            "takeProfitOnFill"
        ][
            "price"
        ]
        == "0.80300"
    )
