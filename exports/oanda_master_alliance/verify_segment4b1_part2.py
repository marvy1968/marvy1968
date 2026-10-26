from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from segment4b.candidate_validator import (
    CandidateValidator,
)
from segment4b.common import (
    load_segment4b_config,
)
from segment4b.models import (
    OrderIntent,
)
from segment4b.payload_builder import (
    DryRunPayloadBuilder,
)


def main() -> None:
    config = load_segment4b_config()

    validator = CandidateValidator(
        config
    )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    candidate = {
        "timestamp": timestamp,
        "instrument": "EUR_USD",
        "side": "buy",
        "alliance_score": 88.0,
        "confirming_experts": 4,
        "reward_risk": 2.0,
        "entry_price": 1.10000,
        "stop_price": 1.09950,
        "target_price": 1.10100,
        "risk": {
            "approved": True,
            "risk_pct": 0.35,
            "position_size": {
                "units": 10000,
                "entry_price": 1.10000,
                "stop_price": 1.09950,
            },
        },
        "authorization": {
            "authorized": True,
            "authorization_id": (
                uuid4().hex[:20]
            ),
            "timestamp": timestamp,
        },
    }

    validation = validator.validate(
        candidate
    )

    assert validation[
        "approved"
    ] is True

    intent = OrderIntent(
        timestamp=timestamp,
        intent_id=uuid4().hex[:20],
        instrument="EUR_USD",
        side="buy",
        units=10000,
        signed_units=10000,
        order_type="MARKET",
        time_in_force="FOK",
        position_fill="DEFAULT",
        entry_price=1.10000,
        stop_price=1.09950,
        target_price=1.10100,
        reward_risk=2.0,
        alliance_score=88.0,
        confirming_experts=4,
        authorization_id=(
            uuid4().hex[:20]
        ),
        client_extension_id=(
            f"ma4b-{uuid4().hex[:12]}"
        ),
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
        "type"
    ] == "MARKET"

    assert order[
        "instrument"
    ] == "EUR_USD"

    assert order[
        "units"
    ] == "10000"

    assert (
        order[
            "stopLossOnFill"
        ][
            "price"
        ]
        == "1.09950"
    )

    assert (
        order[
            "takeProfitOnFill"
        ][
            "price"
        ]
        == "1.10100"
    )

    assert config[
        "automatic_execution"
    ] is False

    assert config[
        (
            "practice_order_"
            "submission_enabled"
        )
    ] is False

    result = {
        "status": "passed",
        "candidate_approved": (
            validation[
                "approved"
            ]
        ),
        "payload_type": (
            order[
                "type"
            ]
        ),
        "payload_instrument": (
            order[
                "instrument"
            ]
        ),
        "payload_units": (
            order[
                "units"
            ]
        ),
        "stop_attached": (
            "stopLossOnFill"
            in order
        ),
        "target_attached": (
            "takeProfitOnFill"
            in order
        ),
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-1 PART 2"
    )
    print("=" * 72)

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4B-1 Part 2 verification passed."
    )


if __name__ == "__main__":
    main()
