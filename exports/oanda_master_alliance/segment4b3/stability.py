from __future__ import annotations

from typing import Any

from segment4b3.common import (
    safe_float,
    utc_now_iso,
)
from segment4b3.metrics import (
    calculate_metrics,
)


class StabilityEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        stability = config.get(
            "stability",
            {},
        )

        self.config = (
            stability
            if isinstance(
                stability,
                dict,
            )
            else {}
        )

    @staticmethod
    def decline_pct(
        baseline: float,
        recent: float,
    ) -> float:
        if baseline <= 0:
            return 0.0

        return max(
            0.0,
            (
                baseline
                - recent
            )
            / abs(
                baseline
            )
            * 100.0,
        )

    def run(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        overall = calculate_metrics(
            records
        )

        requested = self.config.get(
            "rolling_windows",
            [
                20,
                30,
                50,
            ],
        )

        if not isinstance(
            requested,
            list,
        ):
            requested = [
                20,
                30,
                50,
            ]

        windows: list[
            dict[str, Any]
        ] = []

        warnings: list[str] = []
        critical: list[str] = []

        warning_drop = safe_float(
            self.config.get(
                (
                    "degradation_warning_"
                    "drop_pct"
                ),
                20.0,
            )
        )

        critical_drop = safe_float(
            self.config.get(
                (
                    "degradation_critical_"
                    "drop_pct"
                ),
                35.0,
            )
        )

        for requested_size in requested:
            size = int(
                requested_size
            )

            subset = records[
                -size:
            ]

            if len(
                subset
            ) < size:
                windows.append(
                    {
                        "window": size,
                        "eligible": False,
                        "trades": len(
                            subset
                        ),
                        "metrics": (
                            calculate_metrics(
                                subset
                            )
                        ),
                        "warnings": [
                            "insufficient_window_trades"
                        ],
                    }
                )

                continue

            metrics = calculate_metrics(
                subset
            )

            window_warnings: list[
                str
            ] = []

            if metrics[
                "profit_factor"
            ] < safe_float(
                self.config.get(
                    (
                        "minimum_recent_"
                        "profit_factor"
                    ),
                    1.05,
                )
            ):
                window_warnings.append(
                    "recent_profit_factor_below_minimum"
                )

            if metrics[
                "expectancy_r"
            ] < safe_float(
                self.config.get(
                    (
                        "minimum_recent_"
                        "expectancy_r"
                    ),
                    0.02,
                )
            ):
                window_warnings.append(
                    "recent_expectancy_below_minimum"
                )

            if metrics[
                "maximum_drawdown_r"
            ] > safe_float(
                self.config.get(
                    (
                        "maximum_recent_"
                        "drawdown_r"
                    ),
                    7.0,
                )
            ):
                window_warnings.append(
                    "recent_drawdown_above_maximum"
                )

            if metrics[
                "longest_loss_streak"
            ] > int(
                self.config.get(
                    (
                        "maximum_recent_"
                        "loss_streak"
                    ),
                    4,
                )
            ):
                window_warnings.append(
                    "recent_loss_streak_above_maximum"
                )

            expectancy_drop = self.decline_pct(
                safe_float(
                    overall.get(
                        "expectancy_r",
                        0.0,
                    )
                ),
                safe_float(
                    metrics.get(
                        "expectancy_r",
                        0.0,
                    )
                ),
            )

            profit_factor_drop = self.decline_pct(
                safe_float(
                    overall.get(
                        "profit_factor",
                        0.0,
                    )
                ),
                safe_float(
                    metrics.get(
                        "profit_factor",
                        0.0,
                    )
                ),
            )

            maximum_drop = max(
                expectancy_drop,
                profit_factor_drop,
            )

            if maximum_drop >= critical_drop:
                critical.append(
                    (
                        f"window_{size}_"
                        "critical_performance_degradation"
                    )
                )

            elif maximum_drop >= warning_drop:
                warnings.append(
                    (
                        f"window_{size}_"
                        "performance_degradation"
                    )
                )

            windows.append(
                {
                    "window": size,
                    "eligible": True,
                    "trades": len(
                        subset
                    ),
                    "metrics": metrics,
                    "expectancy_drop_pct": (
                        expectancy_drop
                    ),
                    "profit_factor_drop_pct": (
                        profit_factor_drop
                    ),
                    "warnings": (
                        window_warnings
                    ),
                }
            )

            warnings.extend(
                f"window_{size}_{warning}"
                for warning in window_warnings
            )

        if critical:
            status = "critical"

        elif warnings:
            status = "warning"

        elif records:
            status = "stable"

        else:
            status = "insufficient_data"

        return {
            "timestamp": utc_now_iso(),
            "status": status,
            "overall": overall,
            "windows": windows,
            "warnings": sorted(
                set(
                    warnings
                )
            ),
            "critical_reasons": sorted(
                set(
                    critical
                )
            ),
            "passed": status in {
                "stable",
                "insufficient_data",
            },
            "automatic_execution": False,
            "order_submission": False,
        }
