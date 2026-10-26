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

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    load_segment4b4_config,
)
from segment4b4.models import (
    CandidateDecision,
)


def main() -> None:
    config = load_segment4b4_config()

    assert config["research_only"] is True
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

    assert (
        config[
            "broker_order_submission_enabled"
        ]
        is False
    )

    candidate = CandidateDecision(
        instrument="EUR_USD",
        side="buy",
        alliance_score=85.0,
        confirming_experts=4,
        reward_risk=2.0,
        entry_price=1.1000,
        stop_price=1.0995,
        target_price=1.1010,
        units=10000,
        authorization_id="verification",
        source_timestamp=(
            "2026-07-16T00:00:00+00:00"
        ),
    )

    assert candidate.units == 10000
    assert candidate.side == "buy"

    with tempfile.TemporaryDirectory() as directory:
        temp_root = Path(directory)

        test_config = dict(config)
        test_config["files"] = dict(
            config["files"]
        )

        test_config["files"].update(
            {
                "campaign_status": str(
                    temp_root / "status.json"
                ),
                "campaign_control": str(
                    temp_root / "control.json"
                ),
                "campaign_lock": str(
                    temp_root / "campaign.lock"
                ),
                "campaign_pid": str(
                    temp_root / "campaign.pid"
                ),
                "pause_file": str(
                    temp_root / "PAUSED"
                ),
                "shutdown_file": str(
                    temp_root
                    / "SHUTDOWN_REQUEST"
                ),
            }
        )

        state = CampaignState(
            test_config
        )

        initial = state.initial_status()

        assert (
            initial["automatic_execution"]
            is False
        )

        assert (
            initial["order_submission"]
            is False
        )

        state.acquire_lock()

        assert state.lock_path.exists()
        assert state.pid_path.exists()

        state.pause(
            "verification_pause"
        )

        assert state.paused()

        state.resume()

        assert not state.paused()

        state.request_shutdown()

        assert state.shutdown_requested()

        state.clear_shutdown()

        assert not state.shutdown_requested()

        state.release_lock()

        assert not state.lock_path.exists()
        assert not state.pid_path.exists()

    required_files = [
        "config/segment4b4.yaml",
        "segment4b4/__init__.py",
        "segment4b4/common.py",
        "segment4b4/models.py",
        "segment4b4/campaign_state.py",
        "segment4b4/candidate_loader.py",
    ]

    missing = [
        name
        for name in required_files
        if not (ROOT / name).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4B-4 files: "
            + ", ".join(missing)
        )

    result = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "campaign_target": config[
            "campaign"
        ]["target_closed_trades"],
        "observation_checkpoint": config[
            "campaign"
        ]["observation_checkpoint"],
        "practice_only": True,
        "live_environment_allowed": False,
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-4 PART 1"
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
        "Segment 4B-4 Part 1 "
        "verification passed."
    )


if __name__ == "__main__":
    main()
