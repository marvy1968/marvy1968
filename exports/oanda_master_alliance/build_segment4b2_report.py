from __future__ import annotations

from math import isinf

from core.common import ROOT
from segment4b2.common import (
    load_config,
    read_json,
    storage_path,
    utc_now_iso,
)


def number(
    value,
    digits: int = 2,
) -> str:
    try:
        numeric = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return "0.00"

    if isinf(
        numeric
    ):
        return "∞"

    return f"{numeric:.{digits}f}"


def main() -> None:
    config = load_config()

    cycle = read_json(
        storage_path(
            config,
            "latest_cycle",
        ),
        default={},
    )

    performance = read_json(
        storage_path(
            config,
            "performance_summary",
        ),
        default={},
    )

    expert_payload = read_json(
        storage_path(
            config,
            "expert_performance",
        ),
        default={},
    )

    pair_payload = read_json(
        storage_path(
            config,
            "pair_performance",
        ),
        default={},
    )

    setup_payload = read_json(
        storage_path(
            config,
            "setup_performance",
        ),
        default={},
    )

    promotion = read_json(
        storage_path(
            config,
            "promotion_report",
        ),
        default={},
    )

    open_trades = read_json(
        storage_path(
            config,
            "open_trades",
        ),
        default=[],
    )

    overall = performance.get(
        "overall",
        {},
    )

    if not isinstance(
        overall,
        dict,
    ):
        overall = {}

    lines = [
        "# OANDA Master AI — Segment 4B-2",
        "",
        "## Shadow runtime",
        "",
        f"- Generated: **{utc_now_iso()}**",
        (
            "- Latest cycle successful: "
            f"**{cycle.get('successful', False)}**"
        ),
        (
            "- Candidates loaded: "
            f"**{cycle.get('candidates_loaded', 0)}**"
        ),
        (
            "- Candidates approved: "
            f"**{cycle.get('candidates_approved', 0)}**"
        ),
        (
            "- Shadow trades opened: "
            f"**{cycle.get('trades_opened', 0)}**"
        ),
        (
            "- Open trades checked: "
            f"**{cycle.get('open_trades_checked', 0)}**"
        ),
        (
            "- Shadow trades closed: "
            f"**{cycle.get('trades_closed', 0)}**"
        ),
        (
            "- Current open trades: "
            f"**{cycle.get('open_trade_count', 0)}**"
        ),
        (
            "- Total closed trades: "
            f"**{cycle.get('closed_trade_count', 0)}**"
        ),
        "",
        "## Overall performance",
        "",
        (
            "- Trades: "
            f"**{overall.get('trades', 0)}**"
        ),
        (
            "- Wins: "
            f"**{overall.get('wins', 0)}**"
        ),
        (
            "- Losses: "
            f"**{overall.get('losses', 0)}**"
        ),
        (
            "- Pushes: "
            f"**{overall.get('pushes', 0)}**"
        ),
        (
            "- Win rate: "
            f"**{number(100 * float(overall.get('win_rate', 0.0)))}%**"
        ),
        (
            "- Profit factor: "
            f"**{number(overall.get('profit_factor', 0.0))}**"
        ),
        (
            "- Expectancy: "
            f"**{number(overall.get('expectancy_r', 0.0), 3)}R**"
        ),
        (
            "- Net result: "
            f"**{number(overall.get('net_r', 0.0))}R**"
        ),
        (
            "- Maximum drawdown: "
            f"**{number(overall.get('maximum_drawdown_r', 0.0))}R**"
        ),
        (
            "- Longest loss streak: "
            f"**{overall.get('longest_loss_streak', 0)}**"
        ),
        (
            "- Ending shadow equity: "
            f"**${number(performance.get('ending_shadow_equity', 10000.0))}**"
        ),
        "",
        "## Open shadow trades",
        "",
        (
            "| Instrument | Side | Entry | Stop | Target | "
            "Score | Experts | Unrealized R |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    if not isinstance(
        open_trades,
        list,
    ) or not open_trades:
        lines.append(
            "| None | — | — | — | — | — | — | — |"
        )

    else:
        for trade in open_trades:
            if not isinstance(
                trade,
                dict,
            ):
                continue

            lines.append(
                "| "
                f"{trade.get('instrument', '')} | "
                f"{trade.get('side', '')} | "
                f"{trade.get('entry_price', 0)} | "
                f"{trade.get('stop_price', 0)} | "
                f"{trade.get('target_price', 0)} | "
                f"{number(trade.get('alliance_score', 0))} | "
                f"{trade.get('confirming_experts', 0)} | "
                f"{number(trade.get('unrealized_r', 0.0))} |"
            )

    def add_group(
        title: str,
        payload: dict,
        key: str,
    ) -> None:
        records = payload.get(
            key,
            {},
        )

        if not isinstance(
            records,
            dict,
        ):
            records = {}

        lines.extend(
            [
                "",
                f"## {title}",
                "",
                (
                    "| Name | Trades | Win rate | "
                    "Profit factor | Expectancy R | Net R | Drawdown R |"
                ),
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )

        if not records:
            lines.append(
                "| None | 0 | 0.00% | 0.00 | 0.000 | 0.00 | 0.00 |"
            )

            return

        sorted_records = sorted(
            records.items(),
            key=lambda item: float(
                item[1].get(
                    "expectancy_r",
                    0.0,
                )
            ),
            reverse=True,
        )

        for name, metrics in sorted_records:
            lines.append(
                "| "
                f"{name} | "
                f"{metrics.get('trades', 0)} | "
                f"{number(100 * float(metrics.get('win_rate', 0.0)))}% | "
                f"{number(metrics.get('profit_factor', 0.0))} | "
                f"{number(metrics.get('expectancy_r', 0.0), 3)} | "
                f"{number(metrics.get('net_r', 0.0))} | "
                f"{number(metrics.get('maximum_drawdown_r', 0.0))} |"
            )

    add_group(
        "Expert performance",
        expert_payload,
        "experts",
    )

    add_group(
        "Pair performance",
        pair_payload,
        "pairs",
    )

    add_group(
        "Setup performance",
        setup_payload,
        "setups",
    )

    lines.extend(
        [
            "",
            "## Promotion review",
            "",
            (
                "- Eligible for next review: "
                f"**{promotion.get('eligible_for_next_review', False)}**"
            ),
            (
                "- Minimum required trades: "
                f"**{promotion.get('minimum_required_trades', 30)}**"
            ),
            (
                "- Blocking reasons: "
                f"**{', '.join(promotion.get('reasons', [])) or 'none'}**"
            ),
            "",
            "## Guardrails",
            "",
            "- Segment 4B-2 records hypothetical shadow trades only.",
            "- OANDA pricing access is read-only.",
            "- No broker order endpoint is called.",
            "- No trade, position or pending order is created at OANDA.",
            "- Shadow profits and losses are simulated research results.",
            "- Automatic execution remains disabled.",
            "- Practice-order submission remains disabled.",
            "- Live-environment execution remains blocked.",
            "- Promotion eligibility does not automatically enable trading.",
            "",
        ]
    )

    output = (
        ROOT
        / "reports/shadow/"
        "segment4b2_report.md"
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
