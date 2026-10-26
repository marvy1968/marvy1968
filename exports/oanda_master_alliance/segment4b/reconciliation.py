from __future__ import annotations

from typing import Any

from segment4b.broker_adapter import (
    ReadOnlyOandaAdapter,
)
from segment4b.common import (
    append_jsonl,
    atomic_write_json,
    load_segment4b_config,
    read_json,
    storage_path,
    utc_now_iso,
)
from segment4b.models import (
    BrokerReconciliation,
    ReconciliationIssue,
)


class BrokerStateReconciler:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        adapter: ReadOnlyOandaAdapter | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_segment4b_config()
        )

        self.adapter = (
            adapter
            if adapter is not None
            else ReadOnlyOandaAdapter()
        )

    @staticmethod
    def _list(
        payload: dict[str, Any],
        key: str,
    ) -> list[dict[str, Any]]:
        value = payload.get(
            key,
            [],
        )

        if not isinstance(
            value,
            list,
        ):
            return []

        return [
            item
            for item in value
            if isinstance(
                item,
                dict,
            )
        ]

    def _known_state(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        positions_path = storage_path(
            self.config,
            "execution_status",
        ).parent / "known_positions.json"

        orders_path = storage_path(
            self.config,
            "execution_status",
        ).parent / "known_orders.json"

        positions = read_json(
            positions_path,
            default=[],
        )

        orders = read_json(
            orders_path,
            default=[],
        )

        return (
            positions
            if isinstance(
                positions,
                list,
            )
            else [],
            orders
            if isinstance(
                orders,
                list,
            )
            else [],
        )

    @staticmethod
    def _instrument_set(
        records: list[dict[str, Any]],
    ) -> set[str]:
        return {
            str(
                record.get(
                    "instrument",
                    "",
                )
            ).strip().upper()
            for record in records
            if str(
                record.get(
                    "instrument",
                    "",
                )
            ).strip()
        }

    def run(
        self,
    ) -> BrokerReconciliation:
        reconciliation_config = (
            self.config.get(
                "broker_reconciliation",
                {},
            )
        )

        if not isinstance(
            reconciliation_config,
            dict,
        ):
            reconciliation_config = {}

        account_payload = (
            self.adapter.account_summary()
        )

        position_payload = (
            self.adapter.open_positions()
        )

        trade_payload = (
            self.adapter.open_trades()
        )

        order_payload = (
            self.adapter.pending_orders()
        )

        account = account_payload.get(
            "account",
            account_payload,
        )

        broker_positions = self._list(
            position_payload,
            "positions",
        )

        broker_trades = self._list(
            trade_payload,
            "trades",
        )

        broker_orders = self._list(
            order_payload,
            "orders",
        )

        known_positions, known_orders = (
            self._known_state()
        )

        issues: list[
            ReconciliationIssue
        ] = []

        maximum_positions = int(
            reconciliation_config.get(
                "maximum_open_positions",
                5,
            )
        )

        maximum_orders = int(
            reconciliation_config.get(
                "maximum_pending_orders",
                10,
            )
        )

        if len(
            broker_positions
        ) > maximum_positions:
            issues.append(
                ReconciliationIssue(
                    code="too_many_open_positions",
                    severity="critical",
                    message=(
                        "Broker open-position count exceeds "
                        "the configured maximum."
                    ),
                    blocking=True,
                    metadata={
                        "actual": len(
                            broker_positions
                        ),
                        "maximum": (
                            maximum_positions
                        ),
                    },
                )
            )

        if len(
            broker_orders
        ) > maximum_orders:
            issues.append(
                ReconciliationIssue(
                    code="too_many_pending_orders",
                    severity="critical",
                    message=(
                        "Broker pending-order count exceeds "
                        "the configured maximum."
                    ),
                    blocking=True,
                    metadata={
                        "actual": len(
                            broker_orders
                        ),
                        "maximum": maximum_orders,
                    },
                )
            )

        broker_position_symbols = (
            self._instrument_set(
                broker_positions
            )
        )

        known_position_symbols = (
            self._instrument_set(
                known_positions
            )
        )

        unknown_positions = sorted(
            broker_position_symbols
            - known_position_symbols
        )

        broker_order_symbols = (
            self._instrument_set(
                broker_orders
            )
        )

        known_order_symbols = (
            self._instrument_set(
                known_orders
            )
        )

        unknown_orders = sorted(
            broker_order_symbols
            - known_order_symbols
        )

        reject_unknown_positions = bool(
            reconciliation_config.get(
                (
                    "reject_unknown_"
                    "broker_positions"
                ),
                True,
            )
        )

        reject_unknown_orders = bool(
            reconciliation_config.get(
                (
                    "reject_unknown_"
                    "pending_orders"
                ),
                True,
            )
        )

        if (
            unknown_positions
            and reject_unknown_positions
        ):
            issues.append(
                ReconciliationIssue(
                    code="unknown_broker_positions",
                    severity="critical",
                    message=(
                        "OANDA contains open positions not "
                        "registered by Segment 4B."
                    ),
                    blocking=True,
                    metadata={
                        "instruments": (
                            unknown_positions
                        )
                    },
                )
            )

        if (
            unknown_orders
            and reject_unknown_orders
        ):
            issues.append(
                ReconciliationIssue(
                    code="unknown_pending_orders",
                    severity="critical",
                    message=(
                        "OANDA contains pending orders not "
                        "registered by Segment 4B."
                    ),
                    blocking=True,
                    metadata={
                        "instruments": (
                            unknown_orders
                        )
                    },
                )
            )

        passed = not any(
            issue.blocking
            for issue in issues
        )

        report = BrokerReconciliation(
            timestamp=utc_now_iso(),
            passed=passed,
            environment=(
                self.adapter.environment
            ),
            account_id=str(
                account.get(
                    "id",
                    self.adapter.account_id,
                )
            ),
            open_positions=len(
                broker_positions
            ),
            open_trades=len(
                broker_trades
            ),
            pending_orders=len(
                broker_orders
            ),
            known_positions=len(
                known_positions
            ),
            known_orders=len(
                known_orders
            ),
            issues=issues,
            broker_positions=(
                broker_positions
            ),
            broker_trades=broker_trades,
            broker_orders=broker_orders,
            automatic_execution=False,
            order_submission=False,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "latest_reconciliation",
            ),
            report.to_dict(),
        )

        append_jsonl(
            storage_path(
                self.config,
                "reconciliation_history",
            ),
            report.to_dict(),
        )

        return report
