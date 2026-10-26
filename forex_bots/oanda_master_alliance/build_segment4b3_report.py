from __future__ import annotations

from core.common import ROOT
from segment4b3.common import (
    load_config,
    read_json,
    storage_path,
    utc_now_iso,
)


def metric(
    value,
    digits: int = 2,
) -> str:
    try:
        return f"{float(value):.{digits}f}"

    except (
        TypeError,
        ValueError,
    ):
        return f"{0.0:.{digits}f}"


def main() -> None:
    config = load_config()

    analysis = read_json(
        storage_path(
            config,
            "latest_analysis",
        ),
        default={},
    )

    promotion = read_json(
        storage_path(
            config,
            "promotion_decision",
        ),
        default={},
    )

    monte = read_json(
        storage_path(
            config,
            "monte_carlo",
        ),
        default={},
    )

    walk = read_json(
        storage_path(
            config,
            "walk_forward",
        ),
        default={},
    )

    stability = read_json(
        storage_path(
            config,
            "stability",
        ),
        default={},
    )

    hall = read_json(
        storage_path(
            config,
            "hall_of_fame",
        ),
        default={},
    )

    retirement = read_json(
        storage_path(
            config,
            "retirement_watchlist",
        ),
        default={},
    )

    metrics = analysis.get(
        "metrics",
        {},
    )

    if not isinstance(
        metrics,
        dict,
    ):
        metrics = {}

    lines = [
        "# OANDA Master AI — Segment 4B-3",
        "",
        "## Readiness summary",
        "",
        f"- Generated: **{utc_now_iso()}**",
        (
            "- Closed shadow trades: "
            f"**{analysis.get('closed_shadow_trades', 0)}**"
        ),
        (
            "- Open shadow trades: "
            f"**{analysis.get('open_shadow_trades', 0)}**"
        ),
        (
            "- Promotion checkpoint: "
            f"**{promotion.get('checkpoint', 'data_collection')}**"
        ),
        (
            "- Readiness state: "
            f"**{promotion.get('readiness', 'collecting_shadow_data')}**"
        ),
        (
            "- Eligible for manual review: "
            f"**{promotion.get('eligible_for_manual_review', False)}**"
        ),
        "",
        "## Performance",
        "",
        (
            "- Win rate: "
            f"**{metric(100 * float(metrics.get('win_rate', 0.0)))}%**"
        ),
        (
            "- Profit factor: "
            f"**{metric(metrics.get('profit_factor', 0.0))}**"
        ),
        (
            "- Expectancy: "
            f"**{metric(metrics.get('expectancy_r', 0.0), 3)}R**"
        ),
        (
            "- Net result: "
            f"**{metric(metrics.get('net_r', 0.0))}R**"
        ),
        (
            "- Maximum drawdown: "
            f"**{metric(metrics.get('maximum_drawdown_r', 0.0))}R**"
        ),
        (
            "- Longest loss streak: "
            f"**{metrics.get('longest_loss_streak', 0)}**"
        ),
        (
            "- Recovery factor: "
            f"**{metric(metrics.get('recovery_factor', 0.0))}**"
        ),
        "",
        "## Monte Carlo",
        "",
        (
            "- Source trades: "
            f"**{monte.get('source_trades', 0)}**"
        ),
        (
            "- Simulations: "
            f"**{monte.get('simulations', 0)}**"
        ),
        (
            "- Probability profitable: "
            f"**{metric(100 * float(monte.get('probability_profitable', 0.0)))}%**"
        ),
        (
            "- Probability positive expectancy: "
            f"**{metric(100 * float(monte.get('probability_positive_expectancy', 0.0)))}%**"
        ),
        (
            "- Probability of ruin: "
            f"**{metric(100 * float(monte.get('probability_of_ruin', 0.0)))}%**"
        ),
        (
            "- 95th-percentile drawdown: "
            f"**{metric(monte.get('drawdown_percentiles', {}).get('95', 0.0))}R**"
        ),
        (
            "- Monte Carlo passed: "
            f"**{monte.get('passed', False)}**"
        ),
        (
            "- Monte Carlo blockers: "
            f"**{', '.join(monte.get('reasons', [])) or 'none'}**"
        ),
        "",
        "## Walk-forward",
        "",
        (
            "- Eligible: "
            f"**{walk.get('eligible', False)}**"
        ),
        (
            "- Windows: "
            f"**{len(walk.get('windows', []))}**"
        ),
        (
            "- Profitable-window ratio: "
            f"**{metric(100 * float(walk.get('profitable_window_ratio', 0.0)))}%**"
        ),
        (
            "- Positive-expectancy-window ratio: "
            f"**{metric(100 * float(walk.get('positive_expectancy_window_ratio', 0.0)))}%**"
        ),
        (
            "- Failed windows: "
            f"**{walk.get('failed_windows', 0)}**"
        ),
        (
            "- Walk-forward passed: "
            f"**{walk.get('passed', False)}**"
        ),
        (
            "- Walk-forward blockers: "
            f"**{', '.join(walk.get('reasons', [])) or 'none'}**"
        ),
        "",
        "## Stability and degradation",
        "",
        (
            "- Stability status: "
            f"**{stability.get('status', 'unknown')}**"
        ),
        (
            "- Stability passed: "
            f"**{stability.get('passed', False)}**"
        ),
        (
            "- Warnings: "
            f"**{', '.join(stability.get('warnings', [])) or 'none'}**"
        ),
        (
            "- Critical reasons: "
            f"**{', '.join(stability.get('critical_reasons', [])) or 'none'}**"
        ),
        "",
        "## Promotion decision",
        "",
        (
            "- Passed: "
            f"**{promotion.get('passed', False)}**"
        ),
        (
            "- Current trades: "
            f"**{promotion.get('current_trades', 0)}**"
        ),
        (
            "- Required checkpoint trades: "
            f"**{promotion.get('checkpoint_minimum_trades', 30)}**"
        ),
        (
            "- Blocking reasons: "
            f"**{', '.join(promotion.get('reasons', [])) or 'none'}**"
        ),
        (
            "- Automatically promoted: "
            f"**{promotion.get('automatically_promoted', False)}**"
        ),
        "",
        "## Hall of Fame",
        "",
        "| Category | Name | Score | Trades | PF | Expectancy | Net R |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    hall_entries = hall.get(
        "entries",
        [],
    )

    if not isinstance(
        hall_entries,
        list,
    ) or not hall_entries:
        lines.append(
            "| None | — | — | — | — | — | — |"
        )

    else:
        for record in hall_entries:
            metrics_record = record.get(
                "metrics",
                {},
            )

            lines.append(
                "| "
                f"{record.get('category', '')} | "
                f"{record.get('name', '')} | "
                f"{metric(record.get('score', 0.0))} | "
                f"{metrics_record.get('trades', 0)} | "
                f"{metric(metrics_record.get('profit_factor', 0.0))} | "
                f"{metric(metrics_record.get('expectancy_r', 0.0), 3)} | "
                f"{metric(metrics_record.get('net_r', 0.0))} |"
            )

    lines.extend(
        [
            "",
            "## Retirement watchlist",
            "",
            "| Category | Name | Score | Trades | PF | Expectancy | Status |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )

    retirement_entries = retirement.get(
        "entries",
        [],
    )

    if not isinstance(
        retirement_entries,
        list,
    ) or not retirement_entries:
        lines.append(
            "| None | — | — | — | — | — | — |"
        )

    else:
        for record in retirement_entries:
            metrics_record = record.get(
                "metrics",
                {},
            )

            lines.append(
                "| "
                f"{record.get('category', '')} | "
                f"{record.get('name', '')} | "
                f"{metric(record.get('score', 0.0))} | "
                f"{metrics_record.get('trades', 0)} | "
                f"{metric(metrics_record.get('profit_factor', 0.0))} | "
                f"{metric(metrics_record.get('expectancy_r', 0.0), 3)} | "
                f"{record.get('status', '')} |"
            )

    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Segment 4B-3 evaluates completed shadow trades only.",
            "- Monte Carlo results are simulations and are not guaranteed forecasts.",
            "- Walk-forward evaluation does not authorize execution.",
            "- Hall-of-Fame results do not automatically increase expert weights.",
            "- Retirement-watchlist results do not automatically disable experts.",
            "- Promotion eligibility requires a separate manual decision.",
            "- Automatic promotion remains disabled.",
            "- Automatic retirement remains disabled.",
            "- Automatic expert-weight changes remain disabled.",
            "- Practice-order submission remains disabled.",
            "- Live trading remains blocked.",
            "- Orders submitted remain zero.",
            "",
        ]
    )

    output = (
        ROOT
        / "reports/readiness/"
        "segment4b3_report.md"
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
