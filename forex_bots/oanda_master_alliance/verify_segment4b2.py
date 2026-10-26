from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from segment4b2.candidate_engine import (
    ShadowCandidateEngine,
)
from segment4b2.common import (
    load_config,
)
from segment4b2.performance import (
    summary_for_records,
)
from segment4b2.settlement import (
    ShadowSettlementEngine,
)


def main() -> None:
    config = load_config()

    assert config[
        "research_only"
    ] is True

    assert config[
        "practice_only"
    ] is True

    assert config[
        "live_environment_allowed"
    ] is False

    assert config[
        "automatic_execution"
    ] is False

    assert config[
        (
            "practice_order_"
            "submission_enabled"
        )
    ] is False

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    record = {
        "timestamp": timestamp,
        "instrument": "EUR_USD",
        "side": "buy",
        "alliance_score": 88.0,
        "confirming_experts": 4,
        "reward_risk": 2.0,
        "entry_price": 1.10000,
        "stop_price": 1.09950,
        "target_price": 1.10100,
        "regime": "strong_trending",
        "session": "london",
        "expert_signals": [
            {
                "expert": "velez",
                "status": "candidate",
                "setup": "trend_pullback",
            },
            {
                "expert": "raschke",
                "status": "candidate",
                "setup": "momentum_expansion",
            },
        ],
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

    candidate, reasons = (
        ShadowCandidateEngine(
            config
        ).evaluate(
            record
        )
    )

    assert candidate is not None
    assert reasons == []
    assert candidate.instrument == "EUR_USD"
    assert candidate.side == "buy"
    assert "velez" in candidate.experts
    assert "trend_pullback" in candidate.setups

    trade = {
        "shadow_trade_id": "verification",
        "candidate_id": (
            candidate.candidate_id
        ),
        "opened_at": timestamp,
        "status": "open",
        "instrument": "EUR_USD",
        "side": "buy",
        "entry_price": 1.10000,
        "stop_price": 1.09950,
        "target_price": 1.10100,
        "initial_risk_distance": 0.00050,
        "reward_risk": 2.0,
        "highest_price": 1.10000,
        "lowest_price": 1.10000,
        "maximum_favorable_excursion_r": 0.0,
        "maximum_adverse_excursion_r": 0.0,
        "bars_held": 0,
    }

    settled, closed = (
        ShadowSettlementEngine(
            config
        ).evaluate(
            trade,
            {
                "bid": 1.10110,
                "ask": 1.10120,
            },
        )
    )

    assert closed is True
    assert settled[
        "outcome"
    ] == "win"
    assert settled[
        "realized_r"
    ] == 2.0

    metrics = summary_for_records(
        [
            settled,
            {
                **settled,
                "shadow_trade_id": (
                    "verification-loss"
                ),
                "outcome": "loss",
                "realized_r": -1.0,
            },
        ]
    )

    assert metrics[
        "trades"
    ] == 2

    assert metrics[
        "wins"
    ] == 1

    assert metrics[
        "losses"
    ] == 1

    assert metrics[
        "net_r"
    ] == 1.0

    payload = {
        "status": "passed",
        "candidate_validation": True,
        "expert_attribution": True,
        "setup_attribution": True,
        "target_settlement": True,
        "performance_calculation": True,
        "test_trades": metrics[
            "trades"
        ],
        "test_net_r": metrics[
            "net_r"
        ],
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — SEGMENT 4B-2"
    )
    print("=" * 72)
    print(
        json.dumps(
            payload,
            indent=2,
        )
    )
    print()
    print(
        "Segment 4B-2 verification passed."
    )


if __name__ == "__main__":
    main()
