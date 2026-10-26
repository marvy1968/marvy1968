from __future__ import annotations

from typing import Any

from segment4b3.attribution import (
    AttributionEngine,
)
from segment4b3.common import (
    append_jsonl,
    atomic_write_json,
    load_config,
    log_path,
    storage_path,
    utc_now_iso,
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
from segment4b3.source import (
    ShadowResultSource,
)
from segment4b3.stability import (
    StabilityEngine,
)
from segment4b3.walk_forward import (
    WalkForwardEngine,
)


class ReadinessEngine:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_config()
        )

        self.source = ShadowResultSource(
            self.config
        )

    def run(
        self,
    ) -> dict[str, Any]:
        records = (
            self.source.closed_trades()
        )

        open_records = (
            self.source.open_trades()
        )

        metrics = calculate_metrics(
            records
        )

        monte_carlo = MonteCarloEngine(
            self.config
        ).run(
            records
        )

        walk_forward = WalkForwardEngine(
            self.config
        ).run(
            records
        )

        stability = StabilityEngine(
            self.config
        ).run(
            records
        )

        attribution = AttributionEngine(
            self.config
        ).run(
            records
        )

        promotion = PromotionEngine(
            self.config
        ).run(
            metrics=metrics,
            monte_carlo=monte_carlo,
            walk_forward=walk_forward,
            stability=stability,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "monte_carlo",
            ),
            monte_carlo,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "walk_forward",
            ),
            walk_forward,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "stability",
            ),
            stability,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "degradation_status",
            ),
            stability,
        )

        attribution_mapping = {
            "experts": (
                "expert_scorecard"
            ),
            "pairs": (
                "pair_scorecard"
            ),
            "setups": (
                "setup_scorecard"
            ),
            "sessions": (
                "session_scorecard"
            ),
            "regimes": (
                "regime_scorecard"
            ),
        }

        for source_key, storage_key in attribution_mapping.items():
            atomic_write_json(
                storage_path(
                    self.config,
                    storage_key,
                ),
                attribution[
                    source_key
                ],
            )

        atomic_write_json(
            storage_path(
                self.config,
                "promotion_decision",
            ),
            promotion,
        )

        hall_of_fame = self._hall_of_fame(
            attribution
        )

        retirement_watchlist = (
            self._retirement_watchlist(
                attribution
            )
        )

        atomic_write_json(
            storage_path(
                self.config,
                "hall_of_fame",
            ),
            hall_of_fame,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "retirement_watchlist",
            ),
            retirement_watchlist,
        )

        payload = {
            "timestamp": utc_now_iso(),
            "successful": True,
            "closed_shadow_trades": len(
                records
            ),
            "open_shadow_trades": len(
                open_records
            ),
            "metrics": metrics,
            "monte_carlo_passed": (
                monte_carlo.get(
                    "passed",
                    False,
                )
            ),
            "walk_forward_eligible": (
                walk_forward.get(
                    "eligible",
                    False,
                )
            ),
            "walk_forward_passed": (
                walk_forward.get(
                    "passed",
                    False,
                )
            ),
            "stability_status": (
                stability.get(
                    "status",
                    "unknown",
                )
            ),
            "promotion_checkpoint": (
                promotion.get(
                    "checkpoint"
                )
            ),
            "readiness": (
                promotion.get(
                    "readiness"
                )
            ),
            "eligible_for_manual_review": (
                promotion.get(
                    "eligible_for_manual_review",
                    False,
                )
            ),
            "automatic_weight_changes": False,
            "automatic_promotion": False,
            "automatic_retirement": False,
            "automatic_execution": False,
            "order_submission": False,
        }

        atomic_write_json(
            storage_path(
                self.config,
                "latest_analysis",
            ),
            payload,
        )

        append_jsonl(
            log_path(
                self.config,
                "analysis_history",
            ),
            payload,
        )

        append_jsonl(
            log_path(
                self.config,
                "promotion_history",
            ),
            promotion,
        )

        append_jsonl(
            log_path(
                self.config,
                "degradation_history",
            ),
            stability,
        )

        return payload

    @staticmethod
    def _hall_of_fame(
        attribution: dict[str, Any],
    ) -> dict[str, Any]:
        entries: list[
            dict[str, Any]
        ] = []

        for category, payload in attribution.items():
            records = payload.get(
                "records",
                {},
            )

            if not isinstance(
                records,
                dict,
            ):
                continue

            for name, record in records.items():
                if not isinstance(
                    record,
                    dict,
                ):
                    continue

                if (
                    record.get(
                        "eligible",
                        False,
                    )
                    and float(
                        record.get(
                            "score",
                            0.0,
                        )
                    ) >= 70.0
                ):
                    entries.append(
                        {
                            "category": category,
                            "name": name,
                            **record,
                        }
                    )

        entries.sort(
            key=lambda record: float(
                record.get(
                    "score",
                    0.0,
                )
            ),
            reverse=True,
        )

        return {
            "timestamp": utc_now_iso(),
            "entries": entries,
            "automatic_weight_changes": False,
            "automatic_execution": False,
            "order_submission": False,
        }

    @staticmethod
    def _retirement_watchlist(
        attribution: dict[str, Any],
    ) -> dict[str, Any]:
        entries: list[
            dict[str, Any]
        ] = []

        for category, payload in attribution.items():
            records = payload.get(
                "records",
                {},
            )

            if not isinstance(
                records,
                dict,
            ):
                continue

            for name, record in records.items():
                if not isinstance(
                    record,
                    dict,
                ):
                    continue

                metrics = record.get(
                    "metrics",
                    {},
                )

                if not isinstance(
                    metrics,
                    dict,
                ):
                    metrics = {}

                if (
                    record.get(
                        "eligible",
                        False,
                    )
                    and (
                        float(
                            metrics.get(
                                "expectancy_r",
                                0.0,
                            )
                        ) < 0
                        or float(
                            metrics.get(
                                "profit_factor",
                                0.0,
                            )
                        ) < 0.90
                    )
                ):
                    entries.append(
                        {
                            "category": category,
                            "name": name,
                            "status": (
                                "manual_review_required"
                            ),
                            **record,
                        }
                    )

        entries.sort(
            key=lambda record: float(
                record.get(
                    "score",
                    0.0,
                )
            )
        )

        return {
            "timestamp": utc_now_iso(),
            "entries": entries,
            "automatically_retired": False,
            "automatic_execution": False,
            "order_submission": False,
        }
