from __future__ import annotations

import subprocess
from typing import Any

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    ROOT,
    load_segment4b4_config,
    read_json,
    resolve_path,
    utc_now_iso,
)


UNITS = [
    "oanda-master-ai-shadow-campaign.service",
    "oanda-master-ai-shadow-campaign-health.timer",
    "oanda-master-ai-shadow-campaign-snapshot.timer",
    "oanda-master-ai-shadow-campaign-alert.timer",
]


def load_dict(
    path,
) -> dict[str, Any]:
    payload = read_json(
        path,
        default={},
    )

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    return payload


def systemctl_value(
    operation: str,
    unit: str,
) -> str:
    completed = subprocess.run(
        [
            "systemctl",
            operation,
            unit,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    value = (
        completed.stdout.strip()
        or completed.stderr.strip()
    )

    return value or "unknown"


def main() -> None:
    config = load_segment4b4_config()

    state = CampaignState(
        config
    )

    status = state.load()

    health = load_dict(
        resolve_path(
            config,
            "campaign_health",
        )
    )

    base_path = (
        ROOT
        / "state/shadow_campaign"
    )

    snapshot = load_dict(
        base_path
        / "latest_progress_snapshot.json"
    )

    alert = load_dict(
        base_path
        / "latest_checkpoint_alert.json"
    )

    unit_rows = []

    for unit in UNITS:
        unit_rows.append(
            "| "
            + unit
            + " | "
            + systemctl_value(
                "is-active",
                unit,
            )
            + " | "
            + systemctl_value(
                "is-enabled",
                unit,
            )
            + " |"
        )

    lines = [
        "# OANDA Master AI — Segment 4B-4 Part 3",
        "",
        "## Long-running campaign operations",
        "",
        f"- Generated: **{utc_now_iso()}**",
        (
            "- Campaign state: "
            f"**{status.get('state', 'unknown')}**"
        ),
        (
            "- Campaign running: "
            f"**{bool(status.get('running', False))}**"
        ),
        (
            "- Cycle number: "
            f"**{status.get('cycle_number', 0)}**"
        ),
        (
            "- Checkpoint: "
            f"**{status.get('checkpoint', 'data_collection')}**"
        ),
        (
            "- Closed shadow trades: "
            f"**{status.get('closed_shadow_trades', 0)}**"
        ),
        (
            "- Target closed trades: "
            f"**{status.get('target_closed_trades', 250)}**"
        ),
        "",
        "## Campaign health",
        "",
        (
            "- Health present: "
            f"**{bool(health)}**"
        ),
        (
            "- Healthy: "
            f"**{health.get('healthy', 'unknown')}**"
        ),
        (
            "- Stalled: "
            f"**{health.get('stalled', 'unknown')}**"
        ),
        (
            "- Severity: "
            f"**{health.get('severity', 'unknown')}**"
        ),
        (
            "- Status age: "
            f"**{health.get('status_age_seconds', 'unknown')} seconds**"
        ),
        (
            "- Reasons: "
            f"**{', '.join(health.get('reasons', [])) or 'none'}**"
        ),
        (
            "- Warnings: "
            f"**{', '.join(health.get('warnings', [])) or 'none'}**"
        ),
        "",
        "## Progress snapshot",
        "",
        (
            "- Snapshot present: "
            f"**{bool(snapshot)}**"
        ),
        (
            "- Completion: "
            f"**{snapshot.get('completion_percent', 0.0)}%**"
        ),
        (
            "- Remaining trades: "
            f"**{snapshot.get('remaining_trades', 250)}**"
        ),
        (
            "- Checkpoint changed: "
            f"**{snapshot.get('checkpoint_changed', False)}**"
        ),
        (
            "- Alert created: "
            f"**{snapshot.get('alert_created', False)}**"
        ),
        "",
        "## Latest checkpoint alert",
        "",
        (
            "- Alert present: "
            f"**{bool(alert)}**"
        ),
        (
            "- Event: "
            f"**{alert.get('event_type', 'none')}**"
        ),
        (
            "- Severity: "
            f"**{alert.get('severity', 'none')}**"
        ),
        (
            "- Delivered: "
            f"**{alert.get('delivered', False)}**"
        ),
        "",
        "## Services and timers",
        "",
        "| Unit | Active | Enabled |",
        "|---|---|---|",
        *unit_rows,
        "",
        "## Guardrails",
        "",
        "- The campaign remains research-only.",
        "- Shadow trades remain hypothetical.",
        "- Campaign alerts are operational notifications only.",
        "- Checkpoint alerts do not promote strategies.",
        "- Health checks do not authorize execution.",
        "- Automatic promotion remains disabled.",
        "- Automatic execution remains disabled.",
        "- Practice-order submission remains disabled.",
        "- Live trading remains blocked.",
        "- Orders submitted remain zero.",
        "",
    ]

    output = (
        ROOT
        / "reports/shadow_campaign/"
        "segment4b4_part3_report.md"
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(
        "\n".join(lines)
    )


if __name__ == "__main__":
    main()
