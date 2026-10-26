from __future__ import annotations

import random
from typing import Any

from segment4b3.common import (
    percentile,
    safe_float,
    safe_int,
    utc_now_iso,
)
from segment4b3.metrics import (
    maximum_drawdown,
)


class MonteCarloEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        monte = config.get(
            "monte_carlo",
            {},
        )

        self.config = (
            monte
            if isinstance(
                monte,
                dict,
            )
            else {}
        )

    def run(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        values = [
            safe_float(
                record.get(
                    "realized_r",
                    0.0,
                )
            )
            for record in records
        ]

        simulations = safe_int(
            self.config.get(
                "simulations",
                10000,
            ),
            10000,
        )

        random_seed = safe_int(
            self.config.get(
                "random_seed",
                1968,
            ),
            1968,
        )

        rng = random.Random(
            random_seed
        )

        if not values:
            return {
                "timestamp": utc_now_iso(),
                "enabled": bool(
                    self.config.get(
                        "enabled",
                        True,
                    )
                ),
                "simulations": simulations,
                "source_trades": 0,
                "probability_profitable": 0.0,
                "probability_positive_expectancy": 0.0,
                "probability_of_ruin": 0.0,
                "probability_drawdown_over_limit": 0.0,
                "net_r_percentiles": {},
                "drawdown_percentiles": {},
                "passed": False,
                "reasons": [
                    "no_closed_shadow_trades"
                ],
                "automatic_execution": False,
                "order_submission": False,
            }

        net_results: list[float] = []
        drawdowns: list[float] = []
        expectancies: list[float] = []
        ruins = 0

        ruin_threshold = safe_float(
            self.config.get(
                "ruin_threshold_r",
                -20.0,
            ),
            -20.0,
        )

        drawdown_limit = safe_float(
            self.config.get(
                (
                    "maximum_95_percent_"
                    "drawdown_r"
                ),
                15.0,
            ),
            15.0,
        )

        drawdown_over_limit = 0

        for _ in range(
            simulations
        ):
            sample = [
                rng.choice(
                    values
                )
                for _ in range(
                    len(
                        values
                    )
                )
            ]

            net_r = sum(
                sample
            )

            drawdown = maximum_drawdown(
                sample
            )

            expectancy = (
                net_r
                / len(
                    sample
                )
            )

            net_results.append(
                net_r
            )

            drawdowns.append(
                drawdown
            )

            expectancies.append(
                expectancy
            )

            cumulative = 0.0
            minimum_cumulative = 0.0

            for value in sample:
                cumulative += value

                minimum_cumulative = min(
                    minimum_cumulative,
                    cumulative,
                )

            if minimum_cumulative <= ruin_threshold:
                ruins += 1

            if drawdown > drawdown_limit:
                drawdown_over_limit += 1

        probability_profitable = sum(
            1
            for value in net_results
            if value > 0
        ) / simulations

        probability_positive_expectancy = sum(
            1
            for value in expectancies
            if value > 0
        ) / simulations

        probability_of_ruin = (
            ruins
            / simulations
        )

        probability_drawdown_over_limit = (
            drawdown_over_limit
            / simulations
        )

        requested_percentiles = (
            self.config.get(
                "confidence_percentiles",
                [
                    5,
                    25,
                    50,
                    75,
                    95,
                ],
            )
        )

        if not isinstance(
            requested_percentiles,
            list,
        ):
            requested_percentiles = [
                5,
                25,
                50,
                75,
                95,
            ]

        net_percentiles = {
            str(
                percent
            ): percentile(
                net_results,
                float(
                    percent
                ),
            )
            for percent in requested_percentiles
        }

        drawdown_percentiles = {
            str(
                percent
            ): percentile(
                drawdowns,
                float(
                    percent
                ),
            )
            for percent in requested_percentiles
        }

        reasons: list[str] = []

        if probability_profitable < safe_float(
            self.config.get(
                (
                    "minimum_probability_"
                    "profitable"
                ),
                0.70,
            )
        ):
            reasons.append(
                "profitable_probability_below_minimum"
            )

        if (
            probability_positive_expectancy
            < safe_float(
                self.config.get(
                    (
                        "minimum_probability_"
                        "positive_expectancy"
                    ),
                    0.65,
                )
            )
        ):
            reasons.append(
                "positive_expectancy_probability_below_minimum"
            )

        if safe_float(
            drawdown_percentiles.get(
                "95",
                0.0,
            )
        ) > drawdown_limit:
            reasons.append(
                "monte_carlo_95_drawdown_above_maximum"
            )

        if (
            probability_drawdown_over_limit
            > safe_float(
                self.config.get(
                    (
                        "maximum_probability_"
                        "drawdown_over_limit"
                    ),
                    0.20,
                )
            )
        ):
            reasons.append(
                "drawdown_breach_probability_above_maximum"
            )

        if probability_of_ruin > safe_float(
            self.config.get(
                (
                    "maximum_probability_"
                    "of_ruin"
                ),
                0.05,
            )
        ):
            reasons.append(
                "probability_of_ruin_above_maximum"
            )

        return {
            "timestamp": utc_now_iso(),
            "enabled": True,
            "simulations": simulations,
            "source_trades": len(
                values
            ),
            "random_seed": random_seed,
            "probability_profitable": (
                probability_profitable
            ),
            "probability_positive_expectancy": (
                probability_positive_expectancy
            ),
            "probability_of_ruin": (
                probability_of_ruin
            ),
            "probability_drawdown_over_limit": (
                probability_drawdown_over_limit
            ),
            "net_r_percentiles": (
                net_percentiles
            ),
            "drawdown_percentiles": (
                drawdown_percentiles
            ),
            "passed": len(
                reasons
            ) == 0,
            "reasons": reasons,
            "automatic_execution": False,
            "order_submission": False,
        }
