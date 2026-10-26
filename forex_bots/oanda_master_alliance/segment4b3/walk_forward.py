from __future__ import annotations

from typing import Any

from segment4b3.common import (
    safe_float,
    safe_int,
    utc_now_iso,
)
from segment4b3.metrics import (
    calculate_metrics,
)


class WalkForwardEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        walk = config.get(
            "walk_forward",
            {},
        )

        self.config = (
            walk
            if isinstance(
                walk,
                dict,
            )
            else {}
        )

    def run(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        minimum_total = safe_int(
            self.config.get(
                "minimum_total_trades",
                50,
            ),
            50,
        )

        if len(
            records
        ) < minimum_total:
            return {
                "timestamp": utc_now_iso(),
                "enabled": True,
                "source_trades": len(
                    records
                ),
                "minimum_total_trades": (
                    minimum_total
                ),
                "eligible": False,
                "windows": [],
                "profitable_window_ratio": 0.0,
                "positive_expectancy_window_ratio": 0.0,
                "failed_windows": 0,
                "passed": False,
                "reasons": [
                    "insufficient_trades_for_walk_forward"
                ],
                "automatic_execution": False,
                "order_submission": False,
            }

        requested_windows = max(
            1,
            safe_int(
                self.config.get(
                    "rolling_windows",
                    5,
                ),
                5,
            ),
        )

        minimum_window = max(
            1,
            safe_int(
                self.config.get(
                    "minimum_window_trades",
                    10,
                ),
                10,
            ),
        )

        approximate_size = max(
            minimum_window,
            len(
                records
            ) // requested_windows,
        )

        windows: list[
            dict[str, Any]
        ] = []

        start = 0
        window_number = 1

        while start < len(
            records
        ):
            end = min(
                len(
                    records
                ),
                start + approximate_size,
            )

            subset = records[
                start:end
            ]

            if len(
                subset
            ) < minimum_window:
                if windows:
                    prior_start = windows[
                        -1
                    ][
                        "start_index"
                    ]

                    subset = records[
                        prior_start:
                    ]

                    windows.pop()

                    start = prior_start

                else:
                    break

            metrics = calculate_metrics(
                subset
            )

            passed = (
                metrics[
                    "net_r"
                ] > 0
                and metrics[
                    "expectancy_r"
                ] > 0
            )

            windows.append(
                {
                    "window": (
                        window_number
                    ),
                    "start_index": start,
                    "end_index": (
                        start
                        + len(
                            subset
                        )
                        - 1
                    ),
                    "trades": len(
                        subset
                    ),
                    "metrics": metrics,
                    "passed": passed,
                }
            )

            start += len(
                subset
            )

            window_number += 1

        profitable_windows = sum(
            1
            for window in windows
            if safe_float(
                window.get(
                    "metrics",
                    {},
                ).get(
                    "net_r",
                    0.0,
                )
            ) > 0
        )

        positive_expectancy_windows = sum(
            1
            for window in windows
            if safe_float(
                window.get(
                    "metrics",
                    {},
                ).get(
                    "expectancy_r",
                    0.0,
                )
            ) > 0
        )

        failed_windows = sum(
            1
            for window in windows
            if not window.get(
                "passed",
                False,
            )
        )

        total_windows = len(
            windows
        )

        profitable_ratio = (
            profitable_windows
            / total_windows
            if total_windows
            else 0.0
        )

        expectancy_ratio = (
            positive_expectancy_windows
            / total_windows
            if total_windows
            else 0.0
        )

        reasons: list[str] = []

        if profitable_ratio < safe_float(
            self.config.get(
                (
                    "minimum_profitable_"
                    "windows"
                ),
                0.60,
            )
        ):
            reasons.append(
                "profitable_window_ratio_below_minimum"
            )

        if expectancy_ratio < safe_float(
            self.config.get(
                (
                    "minimum_positive_"
                    "expectancy_windows"
                ),
                0.60,
            )
        ):
            reasons.append(
                "positive_expectancy_window_ratio_below_minimum"
            )

        if failed_windows > safe_int(
            self.config.get(
                "maximum_failed_windows",
                2,
            ),
            2,
        ):
            reasons.append(
                "too_many_failed_walk_forward_windows"
            )

        return {
            "timestamp": utc_now_iso(),
            "enabled": True,
            "source_trades": len(
                records
            ),
            "minimum_total_trades": (
                minimum_total
            ),
            "eligible": True,
            "windows": windows,
            "profitable_window_ratio": (
                profitable_ratio
            ),
            (
                "positive_expectancy_"
                "window_ratio"
            ): expectancy_ratio,
            "failed_windows": (
                failed_windows
            ),
            "passed": len(
                reasons
            ) == 0,
            "reasons": reasons,
            "automatic_execution": False,
            "order_submission": False,
        }
