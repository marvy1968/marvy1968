from __future__ import annotations

from statistics import mean
from typing import Any

from segment4b3.common import (
    safe_float,
)


def maximum_drawdown(
    values: list[float],
) -> float:
    cumulative = 0.0
    peak = 0.0
    maximum = 0.0

    for value in values:
        cumulative += value
        peak = max(
            peak,
            cumulative,
        )

        maximum = max(
            maximum,
            peak - cumulative,
        )

    return maximum


def longest_streak(
    values: list[float],
    *,
    losses: bool,
) -> int:
    longest = 0
    current = 0

    for value in values:
        matched = (
            value < 0
            if losses
            else value > 0
        )

        if matched:
            current += 1
            longest = max(
                longest,
                current,
            )

        else:
            current = 0

    return longest


def calculate_metrics(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    results = [
        safe_float(
            record.get(
                "realized_r",
                0.0,
            )
        )
        for record in records
    ]

    wins = sum(
        1
        for value in results
        if value > 0
    )

    losses = sum(
        1
        for value in results
        if value < 0
    )

    pushes = sum(
        1
        for value in results
        if value == 0
    )

    decisive = wins + losses

    gross_profit = sum(
        value
        for value in results
        if value > 0
    )

    gross_loss = abs(
        sum(
            value
            for value in results
            if value < 0
        )
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )

    elif gross_profit > 0:
        profit_factor = 999999.0

    else:
        profit_factor = 0.0

    net_r = sum(
        results
    )

    drawdown = maximum_drawdown(
        results
    )

    return {
        "trades": len(
            results
        ),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": (
            wins
            / decisive
            if decisive
            else 0.0
        ),
        "profit_factor": (
            profit_factor
        ),
        "expectancy_r": (
            mean(
                results
            )
            if results
            else 0.0
        ),
        "net_r": net_r,
        "gross_profit_r": (
            gross_profit
        ),
        "gross_loss_r": (
            gross_loss
        ),
        "maximum_drawdown_r": (
            drawdown
        ),
        "recovery_factor": (
            net_r
            / drawdown
            if drawdown > 0
            else (
                net_r
                if net_r > 0
                else 0.0
            )
        ),
        "longest_win_streak": (
            longest_streak(
                results,
                losses=False,
            )
        ),
        "longest_loss_streak": (
            longest_streak(
                results,
                losses=True,
            )
        ),
        "average_win_r": (
            mean(
                [
                    value
                    for value in results
                    if value > 0
                ]
            )
            if wins
            else 0.0
        ),
        "average_loss_r": (
            mean(
                [
                    value
                    for value in results
                    if value < 0
                ]
            )
            if losses
            else 0.0
        ),
    }
