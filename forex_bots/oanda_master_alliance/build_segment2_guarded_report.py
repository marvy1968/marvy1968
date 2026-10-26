from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent

STATUS_FILE = (
    ROOT
    / "state"
    / "decisions"
    / "segment2_status.json"
)

SHORTLIST_FILE = (
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


def load(
    path: Path,
) -> dict:
    if not path.exists():
        return {}

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


status = load(
    STATUS_FILE
)

shortlist_payload = load(
    SHORTLIST_FILE
)

shortlist = shortlist_payload.get(
    "shortlist",
    [],
)

lines = [
    "# OANDA Master AI — Segment 2",
    "",
    "## Summary",
    "",
    (
        "- Status: "
        f"**{status.get('status', 'not_run')}**"
    ),
    (
        "- Foundation quality passed: "
        f"**{status.get('foundation_quality_passed', False)}**"
    ),
    (
        "- Alliance decisions: "
        f"**{status.get('alliance_decisions', 0)}**"
    ),
    (
        "- Candidate decisions: "
        f"**{status.get('candidate_decisions', 0)}**"
    ),
    (
        "- Correlation-approved shortlist: "
        f"**{status.get('shortlist_count', len(shortlist))}**"
    ),
    "- Automatic execution: **disabled**",
    "- Environment: **practice research**",
    "",
]

failed = status.get(
    "pairs_failed_quality",
    [],
)

if failed:
    lines.extend(
        [
            "## Data-quality block",
            "",
            (
                "The Alliance Brain was not run "
                "because these pairs failed the "
                "foundation quality gate:"
            ),
            "",
        ]
    )

    for symbol in failed:
        lines.append(
            f"- {symbol}"
        )

    lines.append("")

lines.extend(
    [
        "## Shortlist",
        "",
        (
            "| Instrument | Side | Score | "
            "Experts | RR | Regime |"
        ),
        "|---|---|---:|---:|---:|---|",
    ]
)

if not shortlist:
    lines.append(
        "| None | — | — | — | — | — |"
    )
else:
    for item in shortlist:
        if not isinstance(
            item,
            dict,
        ):
            continue

        lines.append(
            "| "
            f"{item.get('instrument', item.get('symbol', ''))} | "
            f"{item.get('side', '')} | "
            f"{item.get('score', '')} | "
            f"{item.get('expert_count', item.get('experts', ''))} | "
            f"{item.get('reward_risk', item.get('rr', ''))} | "
            f"{item.get('regime', '')} |"
        )

lines.extend(
    [
        "",
        "## Guardrails",
        "",
        "- Segment 2 produces research signals only.",
        "- No OANDA order endpoint is called.",
        "- Failed data quality blocks expert decisions.",
        "- Automatic execution remains disabled.",
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
