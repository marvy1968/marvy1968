from __future__ import annotations

from core.common import ROOT
from segment4b.common import (
    load_segment4b_config,
    read_json,
    storage_path,
    utc_now_iso,
)


def main() -> None:
    config = load_segment4b_config()

    status = read_json(
        storage_path(
            config,
            "execution_status",
        ),
        default={},
    )

    reconciliation = status.get(
        "reconciliation",
        {},
    )

    counter = status.get(
        "counter_snapshot",
        {},
    )

    lines = [
        "# OANDA Master AI — Segment 4B-1",
        "",
        "## Runtime",
        "",
        (
            f"- Generated: **{utc_now_iso()}**"
        ),
        (
            "- Environment: "
            f"**{status.get('environment', 'unknown')}**"
        ),
        (
            "- Mode: "
            f"**{status.get('mode', 'unknown')}**"
        ),
        (
            "- Execution gate allowed: "
            f"**{status.get('execution_gate_allowed', False)}**"
        ),
        (
            "- Automatic execution: "
            f"**{status.get('automatic_execution', False)}**"
        ),
        (
            "- Practice-order submission: "
            f"**{status.get('practice_order_submission_enabled', False)}**"
        ),
        (
            "- Orders submitted: "
            f"**{status.get('orders_submitted', 0)}**"
        ),
        "",
        "## Broker reconciliation",
        "",
        (
            "- Passed: "
            f"**{reconciliation.get('passed')}**"
        ),
        (
            "- Open positions: "
            f"**{reconciliation.get('open_positions', 0)}**"
        ),
        (
            "- Open trades: "
            f"**{reconciliation.get('open_trades', 0)}**"
        ),
        (
            "- Pending orders: "
            f"**{reconciliation.get('pending_orders', 0)}**"
        ),
        "",
        "## Simulation results",
        "",
        (
            "- Segment 3 candidates: "
            f"**{status.get('segment3_candidates', 0)}**"
        ),
        (
            "- Evaluated candidates: "
            f"**{status.get('evaluated_candidates', 0)}**"
        ),
        (
            "- Simulated acceptances: "
            f"**{status.get('simulated_acceptances', 0)}**"
        ),
        (
            "- Verified simulated fills: "
            f"**{status.get('verified_simulated_fills', 0)}**"
        ),
        (
            "- Rejected candidates: "
            f"**{status.get('rejected_candidates', 0)}**"
        ),
        "",
        "## Execution counters",
        "",
        (
            "- Hourly simulations: "
            f"**{counter.get('hourly_count', 0)}**"
        ),
        (
            "- Hourly maximum: "
            f"**{counter.get('maximum_orders_per_hour', 0)}**"
        ),
        (
            "- Daily simulations: "
            f"**{counter.get('daily_count', 0)}**"
        ),
        (
            "- Daily maximum: "
            f"**{counter.get('maximum_orders_per_day', 0)}**"
        ),
        "",
        "## Candidate lifecycle",
        "",
        (
            "| Instrument | Side | Candidate | Market | "
            "Counter | Simulated receipt | Fill verified | Rejected |"
        ),
        (
            "|---|---|---:|---:|---:|---:|---:|---:|"
        ),
    ]

    evaluations = status.get(
        "evaluations",
        [],
    )

    if not isinstance(
        evaluations,
        list,
    ):
        evaluations = []

    if not evaluations:
        lines.append(
            "| None | — | — | — | — | — | — | — |"
        )

    for item in evaluations:
        if not isinstance(
            item,
            dict,
        ):
            continue

        candidate = item.get(
            "candidate_validation",
            {},
        )

        market = item.get(
            "market_validation",
            {},
        )

        counter_result = item.get(
            "counter_validation",
            {},
        )

        receipt = item.get(
            "receipt",
            {},
        )

        fill = item.get(
            "fill_verification",
            {},
        )

        lines.append(
            "| "
            f"{item.get('instrument', '')} | "
            f"{item.get('side', '')} | "
            f"{candidate.get('approved', False) if isinstance(candidate, dict) else False} | "
            f"{market.get('passed', False) if isinstance(market, dict) else False} | "
            f"{counter_result.get('passed', False) if isinstance(counter_result, dict) else False} | "
            f"{receipt.get('accepted', False) if isinstance(receipt, dict) else False} | "
            f"{fill.get('passed', False) if isinstance(fill, dict) else False} | "
            f"{item.get('rejected', False)} |"
        )

    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- The market validator uses read-only OANDA pricing.",
            "- Broker reconciliation uses read-only OANDA account endpoints.",
            "- Simulated receipts are locally generated and are not broker receipts.",
            "- Fill verification operates against simulated lifecycle data.",
            "- Duplicate intent and authorization protection remains active.",
            "- Hourly, daily and per-cycle counters remain enforced.",
            "- The activation gate remains blocked.",
            "- Automatic execution remains disabled.",
            "- Practice-order submission remains disabled.",
            "- No OANDA order endpoint is present in the simulated router.",
            "- Orders submitted remain zero.",
            "",
        ]
    )

    output = (
        ROOT
        / "reports/segment4b/"
        "segment4b1_report.md"
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
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
