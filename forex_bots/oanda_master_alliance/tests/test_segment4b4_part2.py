from segment4b4.checkpoints import (
    CampaignCheckpointManager,
)
from segment4b4.common import (
    load_segment4b4_config,
)


def test_checkpoint_progression() -> None:
    config = load_segment4b4_config()

    manager = CampaignCheckpointManager(
        config,
        {},
    )

    assert (
        manager.evaluate(
            0
        ).checkpoint
        == "data_collection"
    )

    assert (
        manager.evaluate(
            30
        ).checkpoint
        == "observation"
    )

    assert (
        manager.evaluate(
            50
        ).checkpoint
        == "provisional"
    )

    assert (
        manager.evaluate(
            100
        ).checkpoint
        == "advanced"
    )

    assert (
        manager.evaluate(
            250
        ).checkpoint
        == "production_review"
    )


def test_target_completion() -> None:
    config = load_segment4b4_config()

    decision = CampaignCheckpointManager(
        config,
        {},
    ).evaluate(
        250
    )

    assert (
        decision.campaign_complete
        is True
    )


def test_execution_remains_disabled() -> None:
    config = load_segment4b4_config()

    assert (
        config[
            "automatic_execution"
        ]
        is False
    )

    assert (
        config[
            "practice_order_submission_enabled"
        ]
        is False
    )
