from __future__ import annotations

import json

from segment4b3.common import (
    load_config,
)
from segment4b3.metrics import (
    calculate_metrics,
)
from segment4b3.monte_carlo import (
    MonteCarloEngine,
)
from segment4b3.promotion import (
    PromotionEngine,
)
from segment4b3.stability import (
    StabilityEngine,
)
from segment4b3.walk_forward import (
    WalkForwardEngine,
)


def synthetic_records() -> list[dict]:
    records = []

    pattern = [
        2.0,
        -1.0,
        1.5,
        -1.0,
        2.0,
    ]

    for index in range(
        100
    ):
        value = pattern[
            index
            % len(
                pattern
            )
        ]

        records.append(
            {
                "shadow_trade_id": (
                    f"verify-{index}"
                ),
                "status": "closed",
                "closed_at": (
                    f"2026-01-{(index % 28) + 1:02d}"
                    "T12:00:00+00:00"
                ),
                "instrument": (
                    "EUR_USD"
                    if index % 2 == 0
                    else "GBP_USD"
                ),
                "experts": [
                    "velez",
                    "raschke",
                ],
                "setups": [
                    "trend_pullback",
                ],
                "session": "london",
                "regime": (
                    "strong_trending"
                ),
                "outcome": (
                    "win"
                    if value > 0
                    else "loss"
                ),
                "realized_r": value,
            }
        )

    return records


def main() -> None:
    config = load_config()

    assert config[
        "research_only"
    ] is True

    assert config[
        "practice_only"
    ] is True

    assert config[
        "live_environment_allowed"
    ] is False

    assert config[
        "automatic_execution"
    ] is False

    assert config[
        (
            "practice_order_"
            "submission_enabled"
        )
    ] is False

    assert config[
        (
            "automatically_change_"
            "expert_weights"
        )
    ] is False

    records = synthetic_records()

    metrics = calculate_metrics(
        records
    )

    assert metrics[
        "trades"
    ] == 100

    assert metrics[
        "net_r"
    ] > 0

    assert metrics[
        "profit_factor"
    ] > 1.0

    monte = MonteCarloEngine(
        config
    ).run(
        records
    )

    assert monte[
        "source_trades"
    ] == 100

    assert monte[
        "simulations"
    ] == 10000

    walk = WalkForwardEngine(
        config
    ).run(
        records
    )

    assert walk[
        "eligible"
    ] is True

    assert len(
        walk[
            "windows"
        ]
    ) > 0

    stability = StabilityEngine(
        config
    ).run(
        records
    )

    assert stability[
        "status"
    ] in {
        "stable",
        "warning",
        "critical",
    }

    promotion = PromotionEngine(
        config
    ).run(
        metrics=metrics,
        monte_carlo=monte,
        walk_forward=walk,
        stability=stability,
    )

    assert promotion[
        "automatically_promoted"
    ] is False

    result = {
        "status": "passed",
        "synthetic_trades": (
            metrics[
                "trades"
            ]
        ),
        "synthetic_net_r": (
            metrics[
                "net_r"
            ]
        ),
        "synthetic_profit_factor": (
            metrics[
                "profit_factor"
            ]
        ),
        "monte_carlo_simulations": (
            monte[
                "simulations"
            ]
        ),
        "walk_forward_windows": len(
            walk[
                "windows"
            ]
        ),
        "stability_status": (
            stability[
                "status"
            ]
        ),
        "promotion_checkpoint": (
            promotion[
                "checkpoint"
            ]
        ),
        "automatic_promotion": False,
        "automatic_weight_changes": False,
        "automatic_execution": False,
        "order_submission": False,
    }

    print()
    print(
        "OANDA MASTER AI — SEGMENT 4B-3"
    )
    print("=" * 72)
    print(
        json.dumps(
            result,
            indent=2,
        )
    )
    print()
    print(
        "Segment 4B-3 verification passed."
    )


if __name__ == "__main__":
    main()
