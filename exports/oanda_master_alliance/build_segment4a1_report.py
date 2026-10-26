from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


from segment4.common import (
    ROOT,
    load_segment4_config,
    read_json,
    resolve_path,
)


def load_jsonl(
    path: Path,
) -> list[
    dict[str, Any]
]:
    if not path.exists():
        return []

    records: list[
        dict[str, Any]
    ] = []

    for line in path.read_text(
        encoding="utf-8",
    ).splitlines():
        if not line.strip():
            continue

        try:
            payload = json.loads(
                line
            )

        except json.JSONDecodeError:
            continue

        if isinstance(
            payload,
            dict,
        ):
            records.append(
                payload
            )

    return records


def main() -> None:
    config = load_segment4_config()
    files = dict(
        config.get(
            "files",
            {},
        )
    )

    status_path = resolve_path(
        str(
            files.get(
                "status",
                (
                    "state/segment4/"
                    "status.json"
                ),
            )
        )
    )

    heartbeat_path = resolve_path(
        str(
            files.get(
                "heartbeat",
                (
                    "state/segment4/"
                    "heartbeat.json"
                ),
            )
        )
    )

    cycle_log_path = resolve_path(
        str(
            files.get(
                "cycle_log",
                (
                    "logs/"
                    "segment4_cycles.jsonl"
                ),
            )
        )
    )

    report_path = resolve_path(
        str(
            files.get(
                "report",
                (
                    "reports/segment4/"
                    "segment4a1_report.md"
                ),
            )
        )
    )

    status = read_json(
        status_path,
        default={},
    )

    heartbeat = read_json(
        heartbeat_path,
        default={},
    )

    cycles = load_jsonl(
        cycle_log_path
    )

    successful_cycles = sum(
        1
        for cycle in cycles
        if bool(
            cycle.get(
                "successful",
                False,
            )
        )
    )

    failed_cycles = (
        len(
            cycles
        )
        - successful_cycles
    )

    submitted_orders = sum(
        int(
            cycle.get(
                "orders_submitted",
                0,
            )
            or 0
        )
        for cycle in cycles
    )

    shortlist_count = sum(
        int(
            cycle.get(
                "shortlist_count",
                0,
            )
            or 0
        )
        for cycle in cycles
    )

    latest_cycle = (
        cycles[-1]
        if cycles
        else {}
    )

    lines = [
        (
            "# OANDA Master AI — "
            "Segment 4A-1"
        ),
        "",
        "## Runtime",
        "",
        (
            "- Installed mode: "
            f"**{config.get('mode')}**"
        ),
        (
            "- Practice only: "
            f"**{config.get('practice_only')}**"
        ),
        (
            "- Automatic execution: "
            f"**{config.get('automatic_execution')}**"
        ),
        (
            "- Practice order submission: "
            f"**{config.get('practice_order_submission_enabled')}**"
        ),
        (
            "- Daemon state: "
            f"**{status.get('state', 'not_run')}**"
        ),
        (
            "- Daemon running: "
            f"**{status.get('daemon_running', False)}**"
        ),
        (
            "- Heartbeat state: "
            f"**{heartbeat.get('state', 'missing')}**"
        ),
        "",
        "## Cycle statistics",
        "",
        (
            "- Recorded cycles: "
            f"**{len(cycles)}**"
        ),
        (
            "- Successful cycles: "
            f"**{successful_cycles}**"
        ),
        (
            "- Failed cycles: "
            f"**{failed_cycles}**"
        ),
        (
            "- Total shortlist observations: "
            f"**{shortlist_count}**"
        ),
        (
            "- Orders submitted: "
            f"**{submitted_orders}**"
        ),
        "",
        "## Latest cycle",
        "",
    ]

    if latest_cycle:
        lines.extend(
            [
                (
                    "- Cycle number: "
                    f"**{latest_cycle.get('cycle_number')}**"
                ),
                (
                    "- Successful: "
                    f"**{latest_cycle.get('successful')}**"
                ),
                (
                    "- Session reason: "
                    f"**{latest_cycle.get('session_reason')}**"
                ),
                (
                    "- Runtime: "
                    f"**{latest_cycle.get('duration_seconds')} seconds**"
                ),
                (
                    "- Shortlist: "
                    f"**{latest_cycle.get('shortlist_count', 0)}**"
                ),
                (
                    "- Risk approved: "
                    f"**{latest_cycle.get('risk_approved_candidates', 0)}**"
                ),
                (
                    "- Orders submitted: "
                    f"**{latest_cycle.get('orders_submitted', 0)}**"
                ),
                (
                    "- Error: "
                    f"**{latest_cycle.get('error') or 'none'}**"
                ),
            ]
        )

    else:
        lines.append(
            "Segment 4A-1 has not recorded a cycle."
        )

    lines.extend(
        [
            "",
            "## Safety",
            "",
            (
                "- Segment 4A-1 orchestrates "
                "Segments 1–3 only."
            ),
            (
                "- It does not independently "
                "authorize a trade."
            ),
            (
                "- Automatic execution remains disabled."
            ),
            (
                "- Practice-order submission remains disabled."
            ),
            (
                "- Live-environment operation is blocked."
            ),
            (
                "- The Segment 3 kill switch and "
                "risk pause remain authoritative."
            ),
            (
                "- A daemon lock prevents duplicate "
                "runtime instances."
            ),
            "",
        ]
    )

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )

    print(
        "\n".join(
            lines
        )
    )


if __name__ == "__main__":
    main()
