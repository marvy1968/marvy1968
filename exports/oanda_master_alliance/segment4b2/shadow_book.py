from __future__ import annotations

import hashlib
from typing import Any

from segment4b2.common import (
    append_jsonl,
    atomic_write_json,
    log_path,
    pip_size,
    read_json,
    storage_path,
    utc_now_iso,
)
from segment4b2.models import (
    ShadowCandidate,
    ShadowTrade,
)


class ShadowTradeBook:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

        self.open_path = storage_path(
            config,
            "open_trades",
        )

        self.closed_path = storage_path(
            config,
            "closed_trades",
        )

    @staticmethod
    def _list_payload(
        payload: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(
            payload,
            list,
        ):
            return []

        return [
            item
            for item in payload
            if isinstance(
                item,
                dict,
            )
        ]

    def open_trades(
        self,
    ) -> list[dict[str, Any]]:
        return self._list_payload(
            read_json(
                self.open_path,
                default=[],
            )
        )

    def closed_trades(
        self,
    ) -> list[dict[str, Any]]:
        return self._list_payload(
            read_json(
                self.closed_path,
                default=[],
            )
        )

    @staticmethod
    def _trade_id(
        candidate: ShadowCandidate,
    ) -> str:
        raw = (
            candidate.candidate_id
            + "|shadow"
        )

        return hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()[:20]

    def open_candidate(
        self,
        candidate: ShadowCandidate,
    ) -> ShadowTrade:
        trade_id = self._trade_id(
            candidate
        )

        existing = self.open_trades()

        for item in existing:
            if item.get(
                "shadow_trade_id"
            ) == trade_id:
                raise ValueError(
                    "shadow_trade_already_open"
                )

        initial_risk = abs(
            candidate.entry_price
            - candidate.stop_price
        )

        if initial_risk <= 0:
            raise ValueError(
                "invalid_shadow_risk_distance"
            )

        trade = ShadowTrade(
            shadow_trade_id=trade_id,
            candidate_id=(
                candidate.candidate_id
            ),
            opened_at=utc_now_iso(),
            status="open",
            instrument=(
                candidate.instrument
            ),
            side=candidate.side,
            alliance_score=(
                candidate.alliance_score
            ),
            confirming_experts=(
                candidate.confirming_experts
            ),
            reward_risk=(
                candidate.reward_risk
            ),
            authorization_id=(
                candidate.authorization_id
            ),
            source_timestamp=(
                candidate.source_timestamp
            ),
            entry_price=(
                candidate.entry_price
            ),
            stop_price=(
                candidate.stop_price
            ),
            target_price=(
                candidate.target_price
            ),
            initial_risk_distance=(
                initial_risk
            ),
            pip_size=pip_size(
                candidate.instrument
            ),
            risk_pct=(
                candidate.risk_pct
            ),
            units=candidate.units,
            experts=candidate.experts,
            setups=candidate.setups,
            regime=candidate.regime,
            session=candidate.session,
            current_price=(
                candidate.entry_price
            ),
            highest_price=(
                candidate.entry_price
            ),
            lowest_price=(
                candidate.entry_price
            ),
            last_checked_at=(
                utc_now_iso()
            ),
            metadata={
                "automatic_execution": False,
                "order_submission": False,
                "source": (
                    "segment4b2_shadow"
                ),
            },
        )

        existing.append(
            trade.to_dict()
        )

        atomic_write_json(
            self.open_path,
            existing,
        )

        append_jsonl(
            log_path(
                self.config,
                "open_history",
            ),
            trade.to_dict(),
        )

        return trade

    def save_open_trades(
        self,
        trades: list[dict[str, Any]],
    ) -> None:
        atomic_write_json(
            self.open_path,
            trades,
        )

    def settle(
        self,
        trade: dict[str, Any],
    ) -> None:
        open_trades = self.open_trades()

        open_trades = [
            item
            for item in open_trades
            if item.get(
                "shadow_trade_id"
            )
            != trade.get(
                "shadow_trade_id"
            )
        ]

        closed = self.closed_trades()

        closed.append(
            trade
        )

        atomic_write_json(
            self.open_path,
            open_trades,
        )

        atomic_write_json(
            self.closed_path,
            closed,
        )

        append_jsonl(
            log_path(
                self.config,
                "settlement_history",
            ),
            trade,
        )
