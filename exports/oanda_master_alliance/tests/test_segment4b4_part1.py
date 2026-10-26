from segment4b4.common import (
    load_segment4b4_config,
)
from segment4b4.models import (
    CandidateDecision,
)


def test_safety_configuration() -> None:
    config = load_segment4b4_config()

    assert config["practice_only"] is True

    assert (
        config["live_environment_allowed"]
        is False
    )

    assert (
        config["automatic_execution"]
        is False
    )

    assert (
        config[
            "practice_order_submission_enabled"
        ]
        is False
    )


def test_candidate_model() -> None:
    candidate = CandidateDecision(
        instrument="NZD_USD",
        side="buy",
        alliance_score=87.0,
        confirming_experts=4,
        reward_risk=2.0,
        entry_price=0.5850,
        stop_price=0.5845,
        target_price=0.5860,
        units=10000,
        authorization_id="pytest",
        source_timestamp=(
            "2026-07-16T00:00:00+00:00"
        ),
    )

    payload = candidate.to_dict()

    assert payload["instrument"] == "NZD_USD"
    assert payload["units"] == 10000
