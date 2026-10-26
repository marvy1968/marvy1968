from __future__ import annotations

import json
from pathlib import Path

from core.common import ROOT


DECISIONS = (
    ROOT
    / "state"
    / "decisions"
    / "latest_alliance_decisions.json"
)

SHORTLIST = (
    ROOT
    / "state"
    / "decisions"
    / "latest_shortlist.json"
)

OUTPUT = (
    ROOT
    / "reports"
    / "segment2"
    / "segment2_report.md"
)


def main() -> None:
    decisions = (
        json.loads(
            DECISIONS.read_text(
                encoding="utf-8"
            )
        )
        if DECISIONS.exists()
        else {
            "decisions": [],
        }
    )

    shortlist = (
        json.loads(
            SHORTLIST.read_text(
                encoding="utf-8"
            )
        )
        if SHORTLIST.exists()
        else {
            "shortlist": [],
        }
    )

    lines = [
        (
            "# OANDA Master AI "
            "— Segment 2"
        ),
        "",
        "## Summary",
        "",
        (
            "- Alliance decisions: "
            f"**{len(decisions.get('decisions', []))}**"
        ),
        (
            "- Candidate decisions: "
            f"**{shortlist.get('candidate_count', 0)}**"
        ),
        (
            "- Correlation-approved shortlist: "
            f"**{shortlist.get('shortlist_count', 0)}**"
        ),
        "- Automatic execution: **disabled**",
        "- Environment: **practice research**",
        "",
        "## Shortlist",
        "",
        (
            "| Instrument | Side | Score | "
            "Experts | RR | Regime |"
        ),
        (
            "|---|---:|---:|---:|---:|---|"
        ),
    ]

    for item in shortlist.get(
        "shortlist",
        [],
    ):
        lines.append(
            "| "
            f"{item['instrument']} | "
            f"{item['side']} | "
            f"{item['alliance_score']:.2f} | "
            f"{item['confirming_experts']} | "
            f"{item['reward_risk']:.2f} | "
            f"{item['regime']} |"
        )

    if not shortlist.get(
        "shortlist"
    ):
        lines.append(
            "| None | — | — | — | — | — |"
        )

    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            (
                "- Segment 2 produces research "
                "signals only."
            ),
            (
                "- No order endpoint is called."
            ),
            (
                "- Overextended lower-timeframe "
                "moves are penalized."
            ),
            (
                "- Mixed regimes may be blocked."
            ),
            (
                "- Currency correlation and "
                "exposure are limited."
            ),
            (
                "- Segment 3 must provide risk, "
                "execution and trade-management "
                "authorization."
            ),
            "",
        ]
    )

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
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
