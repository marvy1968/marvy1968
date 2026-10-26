from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from segment4b.common import (
    load_segment4b_config,
)
from segment4b.execution_counters import (
    ExecutionCounterManager,
)
from segment4b.fill_verifier import (
    SimulatedFillVerifier,
)
from segment4b.models import (
    OrderIntent,
)
from segment4b.payload_builder import (
    DryRunPayloadBuilder,
)
from segment4b.simulated_router import (
    SimulatedPracticeRouter,
)


def main() -> None:
    config = load_segment4b_config()

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    nonce = uuid4().hex[:16]

    intent = OrderIntent(
        timestamp=timestamp,
        intent_id=(
            f"verify-{nonce}"
        ),
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
        alliance_score=90.0,
        confirming_experts=4,
        authorization_id=(
            f"auth-{nonce}"
        ),
        client_extension_id=(
            f"ma4b-{nonce}"
        ),
        source_timestamp=timestamp,
        mode="simulation_only",
    )

    payload = DryRunPayloadBuilder.build(
        intent
    )

    router = SimulatedPracticeRouter(
        config
    )

    receipt = router.simulate(
        intent=intent,
        payload=payload,
    )

    assert receipt.accepted is True
    assert receipt.submitted is False
    assert receipt.order_submission is False

    fill = SimulatedFillVerifier(
        config
    ).verify(
        intent=intent,
        receipt=receipt,
        payload=payload,
    )

    assert fill.passed is True
    assert fill.trade_opened is True
    assert fill.stop_attached is True
    assert fill.target_attached is True
    assert fill.filled_units == 10000

    counters = ExecutionCounterManager(
        config
    )

    counter_validation = (
        counters.evaluate(
            cycle_count=0
        )
    )

    assert (
        counter_validation[
            "maximum_orders_per_cycle"
        ]
        >= 1
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
        "simulated_acceptance": (
            receipt.accepted
        ),
        "actual_submission": (
            receipt.submitted
        ),
        "fill_verified": (
            fill.passed
        ),
        "trade_opened": (
            fill.trade_opened
        ),
        "stop_attached": (
            fill.stop_attached
        ),
        "target_attached": (
            fill.target_attached
        ),
        "expected_units": (
            fill.expected_units
        ),
        "filled_units": (
            fill.filled_units
        ),
        "cycle_limit": (
            counter_validation[
                "maximum_orders_per_cycle"
            ]
        ),
        "hourly_limit": (
            counter_validation[
                "maximum_orders_per_hour"
            ]
        ),
        "daily_limit": (
            counter_validation[
                "maximum_orders_per_day"
            ]
        ),
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-1 PART 3"
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
        "Segment 4B-1 Part 3 verification passed."
    )


if __name__ == "__main__":
    main()
