from __future__ import annotations

from segment4b2.performance import (
    summary_for_records,
)


def test_performance_summary() -> None:
    summary = summary_for_records(
        [
            {
                "outcome": "win",
                "realized_r": 2.0,
                "maximum_favorable_excursion_r": 2.2,
                "maximum_adverse_excursion_r": -0.2,
            },
            {
                "outcome": "loss",
                "realized_r": -1.0,
                "maximum_favorable_excursion_r": 0.3,
                "maximum_adverse_excursion_r": -1.0,
            },
        ]
    )

    assert summary[
        "trades"
    ] == 2

    assert summary[
        "net_r"
    ] == 1.0

    assert summary[
        "expectancy_r"
    ] == 0.5
