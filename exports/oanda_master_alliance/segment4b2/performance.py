from __future__ import annotations

from collections import defaultdict
from math import inf
from statistics import mean
from typing import Any

from segment4b2.common import (
    atomic_write_json,
    safe_float,
    storage_path,
    utc_now_iso,
)


def maximum_drawdown(
    values: list[float],
) -> float:
    peak = -inf
    max_drawdown_value = 0.0

    for value in values:
        peak = max(
            peak,
            value,
        )

        drawdown = (
            peak
            - value
        )

        max_drawdown_value = max(
            max_drawdown_value,
            drawdown,
        )

    return max_drawdown_value


def longest_streak(
    results: list[str],
    target: str,
) -> int:
    longest = 0
    current = 0

    for result in results:
        if result == target:
            current += 1
            longest = max(
                longest,
                current,
            )

        else:
            current = 0

    return longest


def summary_for_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    r_values = [
        safe_float(
            record.get(
                "realized_r",
                0.0,
            )
        )
        for record in records
    ]

    outcomes = [
        str(
            record.get(
                "outcome",
                "",
            )
        ).lower()
        for record in records
    ]

    wins = sum(
        1
        for outcome in outcomes
        if outcome == "win"
    )

    losses = sum(
        1
        for outcome in outcomes
        if outcome == "loss"
    )

    pushes = sum(
        1
        for outcome in outcomes
        if outcome == "push"
    )

    gross_profit = sum(
        value
        for value in r_values
        if value > 0
    )

    gross_loss = abs(
        sum(
            value
            for value in r_values
            if value < 0
        )
    )

    profit_factor = (
        gross_profit
        / gross_loss
        if gross_loss > 0
        else (
            float(
                "inf"
            )
            if gross_profit > 0
            else 0.0
        )
    )

    cumulative = 0.0
    curve = []

    for value in r_values:
        cumulative += value
        curve.append(
            cumulative
        )

    trade_count = len(
        records
    )

    decisive = (
        wins
        + losses
    )

    return {
        "trades": trade_count,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": (
            wins
            / decisive
            if decisive > 0
            else 0.0
        ),
        "profit_factor": (
            profit_factor
        ),
        "expectancy_r": (
            mean(
                r_values
            )
            if r_values
            else 0.0
        ),
        "net_r": sum(
            r_values
        ),
        "gross_profit_r": (
            gross_profit
        ),
        "gross_loss_r": (
            gross_loss
        ),
        "maximum_drawdown_r": (
            maximum_drawdown(
                curve
            )
            if curve
            else 0.0
        ),
        "longest_win_streak": (
            longest_streak(
                outcomes,
                "win",
            )
        ),
        "longest_loss_streak": (
            longest_streak(
                outcomes,
                "loss",
            )
        ),
        "average_mfe_r": (
            mean(
                [
                    safe_float(
                        record.get(
                            (
                                "maximum_favorable_"
                                "excursion_r"
                            ),
                            0.0,
                        )
                    )
                    for record in records
                ]
            )
            if records
            else 0.0
        ),
        "average_mae_r": (
            mean(
                [
                    safe_float(
                        record.get(
                            (
                                "maximum_adverse_"
                                "excursion_r"
                            ),
                            0.0,
                        )
                    )
                    for record in records
                ]
            )
            if records
            else 0.0
        ),
    }


class ShadowPerformanceEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

    @staticmethod
    def _group(
        records: list[dict[str, Any]],
        field: str,
        *,
        list_field: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[
            str,
            list[dict[str, Any]],
        ] = defaultdict(
            list
        )

        for record in records:
            value = record.get(
                field
            )

            if list_field:
                if not isinstance(
                    value,
                    list,
                ):
                    continue

                for item in value:
                    key = str(
                        item
                    ).strip().lower()

                    if key:
                        grouped[
                            key
                        ].append(
                            record
                        )

            else:
                key = str(
                    value,
                ).strip().lower()

                if key:
                    grouped[
                        key
                    ].append(
                        record
                    )

        return dict(
            grouped
        )

    def build(
        self,
        closed: list[dict[str, Any]],
        open_trades: list[dict[str, Any]],
    ) -> dict[str, Any]:
        overall = summary_for_records(
            closed
        )

        equity_config = self.config.get(
            "performance",
            {},
        )

        if not isinstance(
            equity_config,
            dict,
        ):
            equity_config = {}

        starting_equity = safe_float(
            equity_config.get(
                "initial_shadow_equity",
                10000.0,
            )
        )

        risk_unit_value = safe_float(
            equity_config.get(
                "risk_unit_value",
                100.0,
            )
        )

        equity = starting_equity
        equity_curve = []

        for index, trade in enumerate(
            closed,
            start=1,
        ):
            realized_r = safe_float(
                trade.get(
                    "realized_r",
                    0.0,
                )
            )

            equity += (
                realized_r
                * risk_unit_value
            )

            equity_curve.append(
                {
                    "trade_number": index,
                    "timestamp": trade.get(
                        "closed_at"
                    ),
                    "shadow_trade_id": trade.get(
                        "shadow_trade_id"
                    ),
                    "realized_r": realized_r,
                    "cumulative_r": sum(
                        safe_float(
                            item.get(
                                "realized_r",
                                0.0,
                            )
                        )
                        for item in closed[
                            :index
                        ]
                    ),
                    "equity": equity,
                }
            )

        expert_groups = self._group(
            closed,
            "experts",
            list_field=True,
        )

        pair_groups = self._group(
            closed,
            "instrument",
        )

        setup_groups = self._group(
            closed,
            "setups",
            list_field=True,
        )

        expert_performance = {
            key: summary_for_records(
                records
            )
            for key, records in expert_groups.items()
        }

        pair_performance = {
            key: summary_for_records(
                records
            )
            for key, records in pair_groups.items()
        }

        setup_performance = {
            key: summary_for_records(
                records
            )
            for key, records in setup_groups.items()
        }

        promotion = self._promotion_report(
            overall
        )

        payload = {
            "timestamp": utc_now_iso(),
            "overall": overall,
            "open_trades": len(
                open_trades
            ),
            "closed_trades": len(
                closed
            ),
            "starting_shadow_equity": (
                starting_equity
            ),
            "ending_shadow_equity": equity,
            "risk_unit_value": (
                risk_unit_value
            ),
            "automatic_execution": False,
            "order_submission": False,
        }

        atomic_write_json(
            storage_path(
                self.config,
                "performance_summary",
            ),
            payload,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "equity_curve",
            ),
            {
                "timestamp": utc_now_iso(),
                "curve": equity_curve,
                "automatic_execution": False,
                "order_submission": False,
            },
        )

        atomic_write_json(
            storage_path(
                self.config,
                "expert_performance",
            ),
            {
                "timestamp": utc_now_iso(),
                "experts": expert_performance,
            },
        )

        atomic_write_json(
            storage_path(
                self.config,
                "pair_performance",
            ),
            {
                "timestamp": utc_now_iso(),
                "pairs": pair_performance,
            },
        )

        atomic_write_json(
            storage_path(
                self.config,
                "setup_performance",
            ),
            {
                "timestamp": utc_now_iso(),
                "setups": setup_performance,
            },
        )

        atomic_write_json(
            storage_path(
                self.config,
                "promotion_report",
            ),
            promotion,
        )

        return payload

    def _promotion_report(
        self,
        overall: dict[str, Any],
    ) -> dict[str, Any]:
        performance = self.config.get(
            "performance",
            {},
        )

        if not isinstance(
            performance,
            dict,
        ):
            performance = {}

        minimum_trades = max(
            int(
                performance.get(
                    (
                        "minimum_trades_"
                        "for_expert_score"
                    ),
                    10,
                )
            ),
            30,
        )

        reasons: list[str] = []

        trades = int(
            overall.get(
                "trades",
                0,
            )
        )

        if trades < minimum_trades:
            reasons.append(
                "insufficient_shadow_trades"
            )

        profit_factor = safe_float(
            overall.get(
                "profit_factor",
                0.0,
            )
        )

        if profit_factor < safe_float(
            performance.get(
                "minimum_profit_factor",
                1.20,
            )
        ):
            reasons.append(
                "profit_factor_below_minimum"
            )

        expectancy = safe_float(
            overall.get(
                "expectancy_r",
                0.0,
            )
        )

        if expectancy < safe_float(
            performance.get(
                "minimum_expectancy_r",
                0.10,
            )
        ):
            reasons.append(
                "expectancy_below_minimum"
            )

        drawdown = safe_float(
            overall.get(
                "maximum_drawdown_r",
                0.0,
            )
        )

        if drawdown > safe_float(
            performance.get(
                "maximum_drawdown_r",
                10.0,
            )
        ):
            reasons.append(
                "drawdown_above_maximum"
            )

        loss_streak = int(
            overall.get(
                "longest_loss_streak",
                0,
            )
        )

        if loss_streak > int(
            performance.get(
                "maximum_loss_streak",
                5,
            )
        ):
            reasons.append(
                "loss_streak_above_maximum"
            )

        return {
            "timestamp": utc_now_iso(),
            "eligible_for_next_review": (
                len(
                    reasons
                )
                == 0
            ),
            "reasons": reasons,
            "minimum_required_trades": (
                minimum_trades
            ),
            "current_metrics": overall,
            "automatic_execution": False,
            "order_submission": False,
            "message": (
                "This report does not enable practice or live trading."
            ),
        }
