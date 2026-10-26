from __future__ import annotations

from collections import defaultdict
from typing import Any

from segment4b3.common import (
    safe_int,
    utc_now_iso,
)
from segment4b3.metrics import (
    calculate_metrics,
)


class AttributionEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        attribution = config.get(
            "attribution",
            {},
        )

        self.config = (
            attribution
            if isinstance(
                attribution,
                dict,
            )
            else {}
        )

    @staticmethod
    def group_scalar(
        records: list[dict[str, Any]],
        field: str,
    ) -> dict[str, list[dict[str, Any]]]:
        groups: dict[
            str,
            list[dict[str, Any]],
        ] = defaultdict(
            list
        )

        for record in records:
            key = str(
                record.get(
                    field,
                    "",
                )
            ).strip().lower()

            if key:
                groups[
                    key
                ].append(
                    record
                )

        return dict(
            groups
        )

    @staticmethod
    def group_list(
        records: list[dict[str, Any]],
        field: str,
    ) -> dict[str, list[dict[str, Any]]]:
        groups: dict[
            str,
            list[dict[str, Any]],
        ] = defaultdict(
            list
        )

        for record in records:
            values = record.get(
                field,
                [],
            )

            if not isinstance(
                values,
                list,
            ):
                continue

            for value in values:
                key = str(
                    value
                ).strip().lower()

                if key:
                    groups[
                        key
                    ].append(
                        record
                    )

        return dict(
            groups
        )

    def score(
        self,
        groups: dict[str, list[dict[str, Any]]],
        *,
        minimum_trades: int,
    ) -> dict[str, Any]:
        scorecard: dict[
            str,
            Any,
        ] = {}

        for name, records in groups.items():
            metrics = calculate_metrics(
                records
            )

            eligible = (
                metrics[
                    "trades"
                ] >= minimum_trades
            )

            score = 0.0

            if eligible:
                score = (
                    min(
                        100.0,
                        max(
                            0.0,
                            metrics[
                                "profit_factor"
                            ]
                            / 2.0
                            * 100.0,
                        ),
                    )
                    * 0.30
                    + min(
                        100.0,
                        max(
                            0.0,
                            (
                                metrics[
                                    "expectancy_r"
                                ]
                                + 0.50
                            )
                            / 1.50
                            * 100.0,
                        ),
                    )
                    * 0.30
                    + min(
                        100.0,
                        max(
                            0.0,
                            metrics[
                                "win_rate"
                            ]
                            * 100.0,
                        ),
                    )
                    * 0.20
                    + min(
                        100.0,
                        max(
                            0.0,
                            100.0
                            - metrics[
                                "maximum_drawdown_r"
                            ]
                            * 8.0,
                        ),
                    )
                    * 0.20
                )

            scorecard[
                name
            ] = {
                "eligible": eligible,
                "minimum_trades": (
                    minimum_trades
                ),
                "score": round(
                    score,
                    2,
                ),
                "metrics": metrics,
            }

        return {
            "timestamp": utc_now_iso(),
            "records": dict(
                sorted(
                    scorecard.items(),
                    key=lambda item: item[
                        1
                    ][
                        "score"
                    ],
                    reverse=True,
                )
            ),
            "automatic_execution": False,
            "order_submission": False,
        }

    def run(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return {
            "experts": self.score(
                self.group_list(
                    records,
                    "experts",
                ),
                minimum_trades=safe_int(
                    self.config.get(
                        "minimum_expert_trades",
                        15,
                    ),
                    15,
                ),
            ),
            "pairs": self.score(
                self.group_scalar(
                    records,
                    "instrument",
                ),
                minimum_trades=safe_int(
                    self.config.get(
                        "minimum_pair_trades",
                        15,
                    ),
                    15,
                ),
            ),
            "setups": self.score(
                self.group_list(
                    records,
                    "setups",
                ),
                minimum_trades=safe_int(
                    self.config.get(
                        "minimum_setup_trades",
                        15,
                    ),
                    15,
                ),
            ),
            "sessions": self.score(
                self.group_scalar(
                    records,
                    "session",
                ),
                minimum_trades=safe_int(
                    self.config.get(
                        "minimum_session_trades",
                        15,
                    ),
                    15,
                ),
            ),
            "regimes": self.score(
                self.group_scalar(
                    records,
                    "regime",
                ),
                minimum_trades=safe_int(
                    self.config.get(
                        "minimum_regime_trades",
                        15,
                    ),
                    15,
                ),
            ),
        }
