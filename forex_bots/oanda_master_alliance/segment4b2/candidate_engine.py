from __future__ import annotations

import hashlib
from typing import Any

from segment4b2.common import (
    parse_timestamp,
    safe_float,
    safe_int,
    utc_now,
    utc_now_iso,
)
from segment4b2.models import (
    ShadowCandidate,
)


class ShadowCandidateEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

        capture = config.get(
            "capture",
            {},
        )

        self.capture = (
            capture
            if isinstance(
                capture,
                dict,
            )
            else {}
        )

    @staticmethod
    def _experts(
        record: dict[str, Any],
    ) -> list[str]:
        signals = record.get(
            "expert_signals",
            [],
        )

        if not isinstance(
            signals,
            list,
        ):
            return []

        experts: list[str] = []

        for signal in signals:
            if not isinstance(
                signal,
                dict,
            ):
                continue

            status = str(
                signal.get(
                    "status",
                    "",
                )
            ).strip().lower()

            if status not in {
                "candidate",
                "approved",
                "strong",
            }:
                continue

            name = str(
                signal.get(
                    "expert",
                    "",
                )
            ).strip().lower()

            if (
                name
                and name not in experts
            ):
                experts.append(
                    name
                )

        return experts

    @staticmethod
    def _setups(
        record: dict[str, Any],
    ) -> list[str]:
        signals = record.get(
            "expert_signals",
            [],
        )

        if not isinstance(
            signals,
            list,
        ):
            return []

        setups: list[str] = []

        for signal in signals:
            if not isinstance(
                signal,
                dict,
            ):
                continue

            setup = str(
                signal.get(
                    "setup",
                    "",
                )
            ).strip().lower()

            if (
                setup
                and setup not in setups
            ):
                setups.append(
                    setup
                )

        return setups

    @staticmethod
    def _identifier(
        *,
        instrument: str,
        side: str,
        authorization_id: str,
        source_timestamp: str,
    ) -> str:
        raw = "|".join(
            [
                instrument,
                side,
                authorization_id,
                source_timestamp,
            ]
        )

        return hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()[:20]

    def evaluate(
        self,
        record: dict[str, Any],
    ) -> tuple[
        ShadowCandidate | None,
        list[str],
    ]:
        reasons: list[str] = []

        instrument = str(
            record.get(
                "instrument",
                "",
            )
        ).strip().upper()

        side = str(
            record.get(
                "side",
                "",
            )
        ).strip().lower()

        if not instrument:
            reasons.append(
                "missing_instrument"
            )

        if side not in {
            "buy",
            "sell",
        }:
            reasons.append(
                "invalid_side"
            )

        risk = record.get(
            "risk",
            {},
        )

        if not isinstance(
            risk,
            dict,
        ):
            risk = {}

        authorization = record.get(
            "authorization",
            {},
        )

        if not isinstance(
            authorization,
            dict,
        ):
            authorization = {}

        position_size = risk.get(
            "position_size",
            {},
        )

        if not isinstance(
            position_size,
            dict,
        ):
            position_size = {}

        risk_approved = bool(
            risk.get(
                "approved",
                False,
            )
        )

        authorized = bool(
            authorization.get(
                "authorized",
                False,
            )
        )

        if bool(
            self.capture.get(
                "require_risk_approval",
                True,
            )
        ) and not risk_approved:
            reasons.append(
                "risk_not_approved"
            )

        if bool(
            self.capture.get(
                "require_authorization",
                True,
            )
        ) and not authorized:
            reasons.append(
                "authorization_missing"
            )

        alliance_score = safe_float(
            record.get(
                "alliance_score",
                0.0,
            )
        )

        confirming_experts = safe_int(
            record.get(
                "confirming_experts",
                0,
            )
        )

        reward_risk = safe_float(
            record.get(
                "reward_risk",
                0.0,
            )
        )

        if alliance_score < safe_float(
            self.capture.get(
                "minimum_alliance_score",
                72.0,
            )
        ):
            reasons.append(
                "alliance_score_below_minimum"
            )

        if confirming_experts < safe_int(
            self.capture.get(
                "minimum_confirming_experts",
                2,
            )
        ):
            reasons.append(
                "insufficient_confirming_experts"
            )

        if reward_risk < safe_float(
            self.capture.get(
                "minimum_reward_risk",
                1.50,
            )
        ):
            reasons.append(
                "reward_risk_below_minimum"
            )

        entry_price = safe_float(
            position_size.get(
                "entry_price",
                record.get(
                    "entry_price",
                    0.0,
                ),
            )
        )

        stop_price = safe_float(
            position_size.get(
                "stop_price",
                record.get(
                    "stop_price",
                    0.0,
                ),
            )
        )

        target_price = safe_float(
            record.get(
                "target_price",
                0.0,
            )
        )

        if entry_price <= 0:
            reasons.append(
                "invalid_entry_price"
            )

        if (
            bool(
                self.capture.get(
                    "require_valid_stop",
                    True,
                )
            )
            and stop_price <= 0
        ):
            reasons.append(
                "invalid_stop_price"
            )

        if (
            bool(
                self.capture.get(
                    "require_valid_target",
                    True,
                )
            )
            and target_price <= 0
        ):
            reasons.append(
                "invalid_target_price"
            )

        if side == "buy":
            if (
                stop_price >= entry_price
                and stop_price > 0
            ):
                reasons.append(
                    "buy_stop_not_below_entry"
                )

            if (
                target_price <= entry_price
                and target_price > 0
            ):
                reasons.append(
                    "buy_target_not_above_entry"
                )

        elif side == "sell":
            if (
                stop_price <= entry_price
                and stop_price > 0
            ):
                reasons.append(
                    "sell_stop_not_above_entry"
                )

            if (
                target_price >= entry_price
                and target_price > 0
            ):
                reasons.append(
                    "sell_target_not_below_entry"
                )

        source_timestamp = str(
            authorization.get(
                "timestamp",
                record.get(
                    "timestamp",
                    "",
                ),
            )
        )

        parsed_timestamp = parse_timestamp(
            source_timestamp
        )

        if parsed_timestamp is None:
            reasons.append(
                "invalid_source_timestamp"
            )

        else:
            age_minutes = (
                utc_now()
                - parsed_timestamp
            ).total_seconds() / 60.0

            if age_minutes > safe_float(
                self.capture.get(
                    (
                        "maximum_candidate_"
                        "age_minutes"
                    ),
                    30.0,
                )
            ):
                reasons.append(
                    "candidate_stale"
                )

        authorization_id = str(
            authorization.get(
                "authorization_id",
                "",
            )
        ).strip()

        if not authorization_id:
            reasons.append(
                "missing_authorization_id"
            )

        if reasons:
            return (
                None,
                reasons,
            )

        candidate_id = self._identifier(
            instrument=instrument,
            side=side,
            authorization_id=(
                authorization_id
            ),
            source_timestamp=(
                source_timestamp
            ),
        )

        candidate = ShadowCandidate(
            timestamp=utc_now_iso(),
            candidate_id=(
                candidate_id
            ),
            instrument=instrument,
            side=side,
            alliance_score=(
                alliance_score
            ),
            confirming_experts=(
                confirming_experts
            ),
            reward_risk=(
                reward_risk
            ),
            entry_price=(
                entry_price
            ),
            stop_price=(
                stop_price
            ),
            target_price=(
                target_price
            ),
            authorization_id=(
                authorization_id
            ),
            source_timestamp=(
                source_timestamp
            ),
            risk_pct=safe_float(
                risk.get(
                    "risk_pct",
                    0.0,
                )
            ),
            units=abs(
                safe_int(
                    position_size.get(
                        "units",
                        0,
                    )
                )
            ),
            experts=self._experts(
                record
            ),
            setups=self._setups(
                record
            ),
            regime=str(
                record.get(
                    "regime",
                    "",
                )
            ),
            session=str(
                record.get(
                    "session",
                    "",
                )
            ),
            metadata={
                "original_record": (
                    record
                ),
            },
        )

        return (
            candidate,
            [],
        )
