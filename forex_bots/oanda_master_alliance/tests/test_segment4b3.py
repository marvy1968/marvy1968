from __future__ import annotations

from segment4b3.metrics import (
    calculate_metrics,
    maximum_drawdown,
)


def test_metrics() -> None:
    records = [
        {
            "realized_r": 2.0,
        },
        {
            "realized_r": -1.0,
        },
        {
            "realized_r": 1.5,
        },
    ]

    result = calculate_metrics(
        records
    )

    assert result[
        "trades"
    ] == 3

    assert result[
        "net_r"
    ] == 2.5

    assert result[
        "wins"
    ] == 2

    assert result[
        "losses"
    ] == 1


def test_drawdown() -> None:
    assert maximum_drawdown(
        [
            2.0,
            -1.0,
            -1.0,
            2.0,
        ]
    ) == 2.0
