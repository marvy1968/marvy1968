from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from segment4b4.checkpoints import (
    CampaignCheckpointManager,
)
from segment4b4.common import (
    load_segment4b4_config,
)
from segment4b4.shadow_bridge import (
    ShadowStateBridge,
)
from segment4b4.subprocess_runner import (
    SafeStageRunner,
)


def main() -> None:
    config = load_segment4b4_config()

    assert config.get(
        "practice_only"
    ) is True

    assert config.get(
        "live_environment_allowed"
    ) is False

    assert config.get(
        "automatic_execution"
    ) is False

    assert config.get(
        "practice_order_submission_enabled"
    ) is False

    manager = CampaignCheckpointManager(
        config,
        {},
    )

    data_collection = manager.evaluate(
        0
    )

    observation = manager.evaluate(
        30
    )

    provisional = manager.evaluate(
        50
    )

    advanced = manager.evaluate(
        100
    )

    production = manager.evaluate(
        250
    )

    assert (
        data_collection.checkpoint
        == "data_collection"
    )

    assert (
        observation.checkpoint
        == "observation"
    )

    assert (
        provisional.checkpoint
        == "provisional"
    )

    assert (
        advanced.checkpoint
        == "advanced"
    )

    assert (
        production.checkpoint
        == "production_review"
    )

    assert (
        production.campaign_complete
        is True
    )

    runner = SafeStageRunner(
        timeout_seconds=30
    )

    stage = runner.run_python(
        "verification_subprocess",
        "-c",
        (
            "print("
            "'segment4b4 subprocess passed'"
            ")"
        ),
    )

    assert stage.attempted is True
    assert stage.successful is True
    assert stage.return_code == 0

    bridge = ShadowStateBridge()
    bridge_summary = bridge.summary()

    assert (
        bridge_summary[
            "automatic_execution"
        ]
        is False
    )

    assert (
        bridge_summary[
            "order_submission"
        ]
        is False
    )

    required_files = [
        "segment4b4/checkpoints.py",
        "segment4b4/subprocess_runner.py",
        "segment4b4/shadow_bridge.py",
        "segment4b4/campaign_engine.py",
        "run_segment4b4.py",
        "admin/shadow_campaign_control.py",
        "admin/show_shadow_campaign.py",
        "build_segment4b4_report.py",
    ]

    missing = [
        file_name
        for file_name in required_files
        if not (
            ROOT / file_name
        ).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4B-4 Part 2 files: "
            + ", ".join(
                missing
            )
        )

    result = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "observation_checkpoint": (
            observation.checkpoint
        ),
        "provisional_checkpoint": (
            provisional.checkpoint
        ),
        "advanced_checkpoint": (
            advanced.checkpoint
        ),
        "production_checkpoint": (
            production.checkpoint
        ),
        "campaign_complete_at_250": (
            production.campaign_complete
        ),
        "shadow_open_trades": (
            bridge_summary.get(
                "open_trade_count",
                0,
            )
        ),
        "shadow_closed_trades": (
            bridge_summary.get(
                "closed_trade_count",
                0,
            )
        ),
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-4 PART 2"
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
        "Segment 4B-4 Part 2 "
        "verification passed."
    )


if __name__ == "__main__":
    main()
