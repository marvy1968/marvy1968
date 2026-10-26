from __future__ import annotations

from typing import Any

from segment4b2.candidate_engine import (
    ShadowCandidateEngine,
)
from segment4b2.common import (
    append_jsonl,
    atomic_write_json,
    load_config,
    log_path,
    storage_path,
    utc_now_iso,
)
from segment4b2.duplicate_registry import (
    ShadowDuplicateRegistry,
)
from segment4b2.models import (
    ShadowCycleResult,
)
from segment4b2.performance import (
    ShadowPerformanceEngine,
)
from segment4b2.pricing import (
    ShadowPricingReader,
)
from segment4b2.settlement import (
    ShadowSettlementEngine,
)
from segment4b2.shadow_book import (
    ShadowTradeBook,
)
from segment4b2.source_loader import (
    CandidateSourceLoader,
)


class ShadowTradingEngine:
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

        self.source_loader = (
            CandidateSourceLoader(
                self.config
            )
        )

        self.candidate_engine = (
            ShadowCandidateEngine(
                self.config
            )
        )

        self.registry = (
            ShadowDuplicateRegistry(
                self.config
            )
        )

        self.book = ShadowTradeBook(
            self.config
        )

        self.settlement = (
            ShadowSettlementEngine(
                self.config
            )
        )

        self.performance = (
            ShadowPerformanceEngine(
                self.config
            )
        )

    def _capture(
        self,
    ) -> tuple[
        int,
        int,
        int,
        list[str],
    ]:
        records, source_path = (
            self.source_loader.load()
        )

        approved = 0
        opened = 0
        errors: list[str] = []

        capture = self.config.get(
            "capture",
            {},
        )

        if not isinstance(
            capture,
            dict,
        ):
            capture = {}

        maximum_new = int(
            capture.get(
                (
                    "maximum_new_shadow_"
                    "trades_per_cycle"
                ),
                3,
            )
        )

        queue: list[
            dict[str, Any]
        ] = []

        for record in records:
            candidate, reasons = (
                self.candidate_engine.evaluate(
                    record
                )
            )

            if candidate is None:
                queue.append(
                    {
                        "timestamp": utc_now_iso(),
                        "status": "rejected",
                        "reasons": reasons,
                        "record": record,
                    }
                )

                continue

            approved += 1

            queue_record = {
                **candidate.to_dict(),
                "status": "approved",
                "source_path": (
                    str(
                        source_path
                    )
                    if source_path
                    else ""
                ),
            }

            queue.append(
                queue_record
            )

            append_jsonl(
                log_path(
                    self.config,
                    "candidate_history",
                ),
                queue_record,
            )

            if opened >= maximum_new:
                continue

            if self.registry.exists(
                candidate.candidate_id
            ):
                continue

            try:
                trade = self.book.open_candidate(
                    candidate
                )

            except Exception as error:
                errors.append(
                    (
                        f"{candidate.instrument}:"
                        f"{type(error).__name__}:"
                        f"{error}"
                    )
                )

                continue

            self.registry.register(
                candidate.candidate_id,
                {
                    "instrument": (
                        candidate.instrument
                    ),
                    "side": candidate.side,
                    "shadow_trade_id": (
                        trade.shadow_trade_id
                    ),
                },
            )

            opened += 1

        atomic_write_json(
            storage_path(
                self.config,
                "queue",
            ),
            {
                "timestamp": utc_now_iso(),
                "source_path": (
                    str(
                        source_path
                    )
                    if source_path
                    else ""
                ),
                "records": queue,
                "automatic_execution": False,
                "order_submission": False,
            },
        )

        return (
            len(
                records
            ),
            approved,
            opened,
            errors,
        )

    def _monitor(
        self,
    ) -> tuple[
        int,
        int,
        list[str],
    ]:
        open_trades = (
            self.book.open_trades()
        )

        if not open_trades:
            return (
                0,
                0,
                [],
            )

        instruments = sorted(
            {
                str(
                    trade.get(
                        "instrument",
                        "",
                    )
                ).strip().upper()
                for trade in open_trades
                if trade.get(
                    "instrument"
                )
            }
        )

        errors: list[str] = []

        try:
            quotes = (
                ShadowPricingReader().quotes(
                    instruments
                )
            )

        except Exception as error:
            return (
                0,
                0,
                [
                    (
                        "pricing:"
                        f"{type(error).__name__}:"
                        f"{error}"
                    )
                ],
            )

        checked = 0
        closed = 0
        remaining: list[
            dict[str, Any]
        ] = []

        settled: list[
            dict[str, Any]
        ] = []

        for trade in open_trades:
            instrument = str(
                trade.get(
                    "instrument",
                    "",
                )
            ).strip().upper()

            quote = quotes.get(
                instrument
            )

            if not isinstance(
                quote,
                dict,
            ):
                remaining.append(
                    trade
                )

                errors.append(
                    f"{instrument}:missing_quote"
                )

                continue

            updated, is_closed = (
                self.settlement.evaluate(
                    trade,
                    quote,
                )
            )

            checked += 1

            if is_closed:
                closed += 1
                settled.append(
                    updated
                )

            else:
                remaining.append(
                    updated
                )

        self.book.save_open_trades(
            remaining
        )

        for trade in settled:
            closed_records = (
                self.book.closed_trades()
            )

            closed_records.append(
                trade
            )

            atomic_write_json(
                storage_path(
                    self.config,
                    "closed_trades",
                ),
                closed_records,
            )

            append_jsonl(
                log_path(
                    self.config,
                    "settlement_history",
                ),
                trade,
            )

        return (
            checked,
            closed,
            errors,
        )

    def run(
        self,
    ) -> ShadowCycleResult:
        errors: list[str] = []

        loaded = 0
        approved = 0
        opened = 0
        checked = 0
        closed = 0

        try:
            (
                loaded,
                approved,
                opened,
                capture_errors,
            ) = self._capture()

            errors.extend(
                capture_errors
            )

        except Exception as error:
            errors.append(
                (
                    "capture:"
                    f"{type(error).__name__}:"
                    f"{error}"
                )
            )

        try:
            (
                checked,
                closed,
                monitoring_errors,
            ) = self._monitor()

            errors.extend(
                monitoring_errors
            )

        except Exception as error:
            errors.append(
                (
                    "monitor:"
                    f"{type(error).__name__}:"
                    f"{error}"
                )
            )

        open_trades = self.book.open_trades()
        closed_trades = self.book.closed_trades()

        self.performance.build(
            closed_trades,
            open_trades,
        )

        result = ShadowCycleResult(
            timestamp=utc_now_iso(),
            successful=(
                len(
                    errors
                )
                == 0
            ),
            candidates_loaded=loaded,
            candidates_approved=approved,
            trades_opened=opened,
            open_trades_checked=checked,
            trades_closed=closed,
            open_trade_count=len(
                open_trades
            ),
            closed_trade_count=len(
                closed_trades
            ),
            errors=errors,
            automatic_execution=False,
            order_submission=False,
        )

        atomic_write_json(
            storage_path(
                self.config,
                "latest_cycle",
            ),
            result.to_dict(),
        )

        append_jsonl(
            log_path(
                self.config,
                "cycle_history",
            ),
            result.to_dict(),
        )

        for error in errors:
            append_jsonl(
                log_path(
                    self.config,
                    "error_history",
                ),
                {
                    "timestamp": (
                        utc_now_iso()
                    ),
                    "error": error,
                    "automatic_execution": False,
                    "order_submission": False,
                },
            )

        return result
