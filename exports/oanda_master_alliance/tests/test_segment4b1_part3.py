from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

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


def test_simulated_lifecycle() -> None:
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    nonce = uuid4().hex[:12]

    intent = OrderIntent(
        timestamp=timestamp,
        intent_id=(
            f"pytest-{nonce}"
        ),
        instrument="USD_CHF",
        side="sell",
        units=5000,
        signed_units=-5000,
        order_type="MARKET",
        time_in_force="FOK",
        position_fill="DEFAULT",
        entry_price=0.80500,
        stop_price=0.80600,
        target_price=0.80300,
        reward_risk=2.0,
        alliance_score=88.0,
        confirming_experts=4,
        authorization_id=(
            f"pytest-auth-{nonce}"
        ),
        client_extension_id=(
            f"pytest-{nonce}"
        ),
        source_timestamp=timestamp,
        mode="simulation_only",
    )

    payload = DryRunPayloadBuilder.build(
        intent
    )

    receipt = SimulatedPracticeRouter().simulate(
        intent=intent,
        payload=payload,
    )

    assert receipt.submitted is False
    assert receipt.accepted is True

    fill = SimulatedFillVerifier().verify(
        intent=intent,
        receipt=receipt,
        payload=payload,
    )

    assert fill.passed is True
    assert fill.filled_units == 5000
    assert fill.stop_attached is True
    assert fill.target_attached is True
