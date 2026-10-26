from __future__ import annotations

from typing import Any

from segment4b4.campaign_state import (
    CampaignState,
)
from segment4b4.common import (
    ROOT,
    load_segment4b4_config,
    read_json,
    utc_now_iso,
)
from segment4b4.shadow_bridge import (
    ShadowStateBridge,
)


def text(
    value: Any,
    default: str = "unknown",
) -> str:
    if value is None:
        return default

    return str(value)


def boolean(
    value: Any,
) -> str:
    return (
        "True"
        if bool(value)
        else "False"
    )


def main() -> None:
    config = load_segment4b4_config()

    state = CampaignState(
        config
    )

    status = state.load()

    control = read_json(
        state.control_path,
        default={},
    )

    if not isinstance(
        control,
        dict,
    ):
        control = {}

    bridge = ShadowStateBridge()
    shadow = bridge.summary()

    last_cycle = status.get(
        "last_cycle",
        {},
    )

    if not isinstance(
        last_cycle,
        dict,
    ):
        last_cycle = {}

    stages = status.get(
        "stages",
        [],
    )

    if not isinstance(
        stages,
        list,
    ):
        stages = []

    stage_rows: list[str] = []

    for stage in stages:
        if not isinstance(
            stage,
            dict,
        ):
            continue

        stage_rows.append(
            "| "
            + text(
                stage.get(
                    "name"
                )
            )
            + " | "
            + boolean(
                stage.get(
                    "attempted"
                )
            )
            + " | "
            + boolean(
                stage.get(
                    "successful"
                )
            )
            + " | "
            + text(
                stage.get(
                    "return_code",
                    0,
                )
            )
            + " | "
            + text(
                stage.get(
                    "duration_seconds",
                    0,
                )
            )
            + " | "
            + (
                text(
                    stage.get(
                        "error"
                    ),
                    "none",
                )
                or "none"
            )
            + " |"
        )

    if not stage_rows:
        stage_rows.append(
            "| None | — | — | — | — | — |"
        )

    errors = last_cycle.get(
        "errors",
        [],
    )

    if not isinstance(
        errors,
        list,
    ):
        errors = []

    error_text = (
        ", ".join(
            str(item)
            for item in errors
        )
        if errors
        else "none"
    )

    lines = [
        "# OANDA Master AI — Segment 4B-4",
        "",
        "## Shadow campaign runtime",
        "",
        (
            "- Generated: "
            f"**{utc_now_iso()}**"
        ),
        (
            "- State: "
            f"**{text(status.get('state'))}**"
        ),
        (
            "- Running: "
            f"**{boolean(status.get('running'))}**"
        ),
        (
            "- Cycle number: "
            f"**{status.get('cycle_number', 0)}**"
        ),
        (
            "- Last cycle successful: "
            f"**{boolean(status.get('last_cycle_successful'))}**"
        ),
        (
            "- Consecutive errors: "
            f"**{status.get('consecutive_errors', 0)}**"
        ),
        (
            "- Paused: "
            f"**{boolean(status.get('paused'))}**"
        ),
        (
            "- Shutdown requested: "
            f"**{boolean(status.get('shutdown_requested'))}**"
        ),
        "",
        "## Campaign progress",
        "",
        (
            "- Current checkpoint: "
            f"**{text(status.get('checkpoint'), 'data_collection')}**"
        ),
        (
            "- Open shadow trades: "
            f"**{status.get('open_shadow_trades', shadow.get('open_trade_count', 0))}**"
        ),
        (
            "- Closed shadow trades: "
            f"**{status.get('closed_shadow_trades', shadow.get('closed_trade_count', 0))}**"
        ),
        (
            "- Target closed trades: "
            f"**{status.get('target_closed_trades', 250)}**"
        ),
        (
            "- Campaign complete: "
            f"**{boolean(status.get('campaign_complete'))}**"
        ),
        (
            "- Checkpoint stop requested: "
            f"**{boolean(status.get('checkpoint_stop_requested'))}**"
        ),
        (
            "- Checkpoint reason: "
            f"**{text(status.get('checkpoint_reason'), 'none')}**"
        ),
        "",
        "## Latest cycle",
        "",
        (
            "- Candidates loaded: "
            f"**{last_cycle.get('candidates_loaded', 0)}**"
        ),
        (
            "- Candidates approved: "
            f"**{last_cycle.get('candidates_approved', 0)}**"
        ),
        (
            "- Shadow trades opened: "
            f"**{last_cycle.get('shadow_trades_opened', 0)}**"
        ),
        (
            "- Open trades checked: "
            f"**{last_cycle.get('open_trades_checked', 0)}**"
        ),
        (
            "- Shadow trades closed: "
            f"**{last_cycle.get('shadow_trades_closed', 0)}**"
        ),
        (
            "- Errors: "
            f"**{error_text}**"
        ),
        "",
        "## Pipeline stages",
        "",
        (
            "| Stage | Attempted | Successful | "
            "Return code | Runtime seconds | Error |"
        ),
        "|---|---:|---:|---:|---:|---|",
        *stage_rows,
        "",
        "## Campaign controls",
        "",
        (
            "- Campaign enabled: "
            f"**{boolean(control.get('campaign_enabled', True))}**"
        ),
        (
            "- Manual pause: "
            f"**{boolean(control.get('manual_pause', False))}**"
        ),
        (
            "- Stop at observation checkpoint: "
            f"**{boolean(control.get('stop_at_observation_checkpoint', False))}**"
        ),
        (
            "- Stop at provisional checkpoint: "
            f"**{boolean(control.get('stop_at_provisional_checkpoint', False))}**"
        ),
        (
            "- Stop at advanced checkpoint: "
            f"**{boolean(control.get('stop_at_advanced_checkpoint', False))}**"
        ),
        (
            "- Stop at production-review checkpoint: "
            f"**{boolean(control.get('stop_at_production_review_checkpoint', True))}**"
        ),
        "",
        "## Guardrails",
        "",
        "- Segment 4B-4 orchestrates research and shadow-analysis stages only.",
        "- Shadow trades are hypothetical and do not create broker positions.",
        "- Campaign checkpoints do not authorize practice or live execution.",
        "- Automatic promotion remains disabled.",
        "- Automatic execution remains disabled.",
        "- Practice-order submission remains disabled.",
        "- Live-environment execution remains blocked.",
        "- Orders submitted remain zero.",
        "",
    ]

    output = (
        ROOT
        / "reports/shadow_campaign/"
        "segment4b4_report.md"
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
