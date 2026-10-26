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

from segment4b4.campaign_health import (
    CampaignHealthMonitor,
)
from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.checkpoint_alerts import (
    CheckpointAlertManager,
)
from segment4b4.common import (
    atomic_write_json,
    load_segment4b4_config,
)
from segment4b4.progress_snapshot import (
    CampaignProgressRecorder,
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

    state = CampaignState(
        config
    )

    status = state.load()

    assert isinstance(
        status,
        dict,
    )

    health = (
        CampaignHealthMonitor(
            config
        )
        .evaluate()
    )

    assert (
        health.automatic_execution
        is False
    )

    assert (
        health.order_submission
        is False
    )

    snapshot = (
        CampaignProgressRecorder(
            config
        )
        .create()
    )

    assert (
        snapshot.automatic_promotion
        is False
    )

    assert (
        snapshot.automatic_execution
        is False
    )

    assert (
        snapshot.order_submission
        is False
    )

    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(
            directory
        )

        alert_path = (
            temporary
            / "latest_alert.json"
        )

        atomic_write_json(
            alert_path,
            {
                "event_type": (
                    "verification_checkpoint"
                ),
                "checkpoint": (
                    "observation"
                ),
                "closed_shadow_trades": 30,
                "target_closed_trades": 250,
                "severity": "info",
                "message": (
                    "Verification checkpoint alert."
                ),
            },
        )

        manager = (
            CheckpointAlertManager()
        )

        original_latest = (
            manager.latest_alert_path
        )

        original_delivery = (
            manager.delivery_state_path
        )

        original_history = (
            manager.delivery_history_path
        )

        original_log = (
            manager.alert_log_path
        )

        manager.latest_alert_path = (
            alert_path
        )

        manager.delivery_state_path = (
            temporary
            / "delivery_state.json"
        )

        manager.delivery_history_path = (
            temporary
            / "delivery_history.jsonl"
        )

        manager.alert_log_path = (
            temporary
            / "alerts.log"
        )

        first = manager.deliver()
        second = manager.deliver()

        assert first.delivered is True
        assert first.suppressed is False

        assert second.delivered is False
        assert second.suppressed is True

        manager.latest_alert_path = (
            original_latest
        )

        manager.delivery_state_path = (
            original_delivery
        )

        manager.delivery_history_path = (
            original_history
        )

        manager.alert_log_path = (
            original_log
        )

    required_files = [
        "segment4b4/campaign_health.py",
        "segment4b4/progress_snapshot.py",
        "segment4b4/checkpoint_alerts.py",
        "run_segment4b4_health.py",
        "run_segment4b4_snapshot.py",
        (
            "run_segment4b4_"
            "checkpoint_alerts.py"
        ),
        (
            "admin/"
            "show_shadow_campaign_operations.py"
        ),
        "build_segment4b4_part3_report.py",
        (
            "deployment/systemd/"
            "oanda-master-ai-shadow-"
            "campaign.service"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-shadow-"
            "campaign-health.timer"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-shadow-"
            "campaign-snapshot.timer"
        ),
        (
            "deployment/systemd/"
            "oanda-master-ai-shadow-"
            "campaign-alert.timer"
        ),
        (
            "deployment/scripts/"
            "install_shadow_campaign_units.sh"
        ),
    ]

    missing = [
        file_name
        for file_name in required_files
        if not (
            ROOT
            / file_name
        ).exists()
    ]

    if missing:
        raise AssertionError(
            "Missing Segment 4B-4 Part 3 files: "
            + ", ".join(
                missing
            )
        )

    result = {
        "status": "passed",
        "required_files": len(
            required_files
        ),
        "campaign_state": status.get(
            "state",
            "unknown",
        ),
        "health_generated": True,
        "health_healthy": (
            health.healthy
        ),
        "health_stalled": (
            health.stalled
        ),
        "snapshot_generated": True,
        "snapshot_checkpoint": (
            snapshot.checkpoint
        ),
        "snapshot_completion_percent": (
            snapshot.completion_percent
        ),
        "alert_first_delivery": (
            first.delivered
        ),
        "alert_duplicate_suppressed": (
            second.suppressed
        ),
        "automatic_promotion": False,
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — "
        "SEGMENT 4B-4 PART 3"
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
        "Segment 4B-4 Part 3 "
        "verification passed."
    )


if __name__ == "__main__":
    main()
