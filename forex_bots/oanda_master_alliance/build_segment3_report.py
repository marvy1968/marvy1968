from __future__ import annotations

import json
from pathlib import Path

from core.common import ROOT


def main() -> None:
    status_path = (
        ROOT
        / "state/execution/"
        "segment3_status.json"
    )

    if not status_path.exists():
        raise SystemExit(
            "Segment 3 has not run."
        )

    status = json.loads(
        status_path.read_text(
            encoding="utf-8"
        )
    )

    lines = [
        "# OANDA Master AI — Segment 3",
        "",
        "## Runtime",
        "",
        (
            "- Environment: "
            f"**{status.get('environment')}**"
        ),
        (
            "- Practice only: "
            f"**{status.get('practice_only')}**"
        ),
        (
            "- Automatic execution: "
            f"**{status.get('automatic_execution')}**"
        ),
        (
            "- Practice order submission enabled: "
            f"**{status.get('practice_order_submission_enabled')}**"
        ),
        (
            "- Submit requested: "
            f"**{status.get('submit_requested')}**"
        ),
        (
            "- Candidates evaluated: "
            f"**{status.get('evaluated_candidates', 0)}**"
        ),
        (
            "- Risk approved: "
            f"**{status.get('risk_approved_candidates', 0)}**"
        ),
        (
            "- Orders submitted: "
            f"**{status.get('submitted_orders', 0)}**"
        ),
        "",
        "## Candidate decisions",
        "",
        (
            "| Instrument | Side | Score | "
            "Risk approved | Units | Authorization | Reasons |"
        ),
        "|---|---|---:|---|---:|---|---|",
    ]

    for result in status.get(
        "results",
        [],
    ):
        risk = result.get(
            "risk",
            {},
        )
        authorization = result.get(
            "authorization",
            {},
        )
        size = risk.get(
            "position_size"
        ) or {}

        reasons = (
            risk.get(
                "reasons",
                [],
            )
            + authorization.get(
                "reasons",
                [],
            )
        )

        lines.append(
            "| "
            f"{result.get('instrument')} | "
            f"{result.get('side')} | "
            f"{result.get('alliance_score')} | "
            f"{risk.get('approved')} | "
            f"{size.get('signed_units', 0)} | "
            f"{authorization.get('authorized')} | "
            f"{', '.join(reasons) or 'approved'} |"
        )

    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            (
                "- Segment 2 candidates are "
                "revalidated before authorization."
            ),
            (
                "- Position size uses account equity, "
                "risk percentage and stop distance."
            ),
            (
                "- Daily, weekly, total-open-risk and "
                "currency-exposure limits remain active."
            ),
            (
                "- Broker stop-loss and take-profit "
                "attachments are required."
            ),
            (
                "- Duplicate signals and duplicate "
                "instrument positions are blocked."
            ),
            (
                "- Practice submission requires both "
                "`automatic_execution` and "
                "`practice_order_submission_enabled`."
            ),
            (
                "- The kill-switch file blocks every "
                "new authorization."
            ),
            "",
        ]
    )

    output = (
        ROOT
        / "reports/segment3/"
        "segment3_report.md"
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
