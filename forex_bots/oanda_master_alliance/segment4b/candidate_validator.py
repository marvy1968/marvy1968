from __future__ import annotations

from typing import Any

from core.common import ROOT
from segment4b.common import (
    age_minutes,
    load_segment4b_config,
    read_json,
)


class CandidateValidator:
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
            else load_segment4b_config()
        )

        requirements = self.config.get(
            "candidate_requirements",
            {},
        )

        self.requirements = (
            requirements
            if isinstance(
                requirements,
                dict,
            )
            else {}
        )

    def latest_segment3(
        self,
    ) -> dict[str, Any]:
        candidates = [
            (
                ROOT
                / "state/execution/"
                "segment3_status.json"
            ),
            (
                ROOT
                / "state/execution/"
                "latest_segment3.json"
            ),
            (
                ROOT
                / "state/execution/"
                "segment3_latest.json"
            ),
        ]

        for path in candidates:
            payload = read_json(
                path,
                default={},
            )

            if (
                isinstance(
                    payload,
                    dict,
                )
                and payload
            ):
                return payload

        raise FileNotFoundError(
            "No Segment 3 status payload was found."
        )

    def validate(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        warnings: list[str] = []

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
                "instrument_missing"
            )

        if side not in {
            "buy",
            "sell",
        }:
            reasons.append(
                "side_invalid"
            )

        score = float(
            record.get(
                "alliance_score",
                0.0,
            )
        )

        minimum_score = float(
            self.requirements.get(
                "minimum_alliance_score",
                80.0,
            )
        )

        if score < minimum_score:
            reasons.append(
                "alliance_score_below_minimum"
            )

        experts = int(
            record.get(
                "confirming_experts",
                0,
            )
        )

        minimum_experts = int(
            self.requirements.get(
                "minimum_confirming_experts",
                3,
            )
        )

        if experts < minimum_experts:
            reasons.append(
                "confirming_experts_below_minimum"
            )

        reward_risk = float(
            record.get(
                "reward_risk",
                0.0,
            )
        )

        minimum_rr = float(
            self.requirements.get(
                "minimum_reward_risk",
                1.75,
            )
        )

        if reward_risk < minimum_rr:
            reasons.append(
                "reward_risk_below_minimum"
            )

        source_timestamp = (
            record.get(
                "timestamp"
            )
            or record.get(
                "source_timestamp"
            )
            or (
                record.get(
                    "authorization",
                    {}
                ).get(
                    "timestamp"
                )
                if isinstance(
                    record.get(
                        "authorization"
                    ),
                    dict,
                )
                else None
            )
        )

        candidate_age = age_minutes(
            source_timestamp
        )

        maximum_age = float(
            self.requirements.get(
                (
                    "maximum_candidate_"
                    "age_minutes"
                ),
                10,
            )
        )

        if candidate_age is None:
            reasons.append(
                "candidate_timestamp_invalid"
            )

        elif candidate_age > maximum_age:
            reasons.append(
                "candidate_stale"
            )

        risk = record.get(
            "risk",
            {},
        )

        authorization = record.get(
            "authorization",
            {},
        )

        if bool(
            self.requirements.get(
                "require_risk_approval",
                True,
            )
        ):
            if not (
                isinstance(
                    risk,
                    dict,
                )
                and bool(
                    risk.get(
                        "approved",
                        False,
                    )
                )
            ):
                reasons.append(
                    "risk_not_approved"
                )

        if bool(
            self.requirements.get(
                (
                    "require_segment3_"
                    "authorization"
                ),
                True,
            )
        ):
            if not (
                isinstance(
                    authorization,
                    dict,
                )
                and bool(
                    authorization.get(
                        "authorized",
                        False,
                    )
                )
            ):
                reasons.append(
                    "segment3_not_authorized"
                )

        position_size = (
            risk.get(
                "position_size",
                {},
            )
            if isinstance(
                risk,
                dict,
            )
            else {}
        )

        units = int(
            position_size.get(
                "units",
                0,
            )
        )

        if units <= 0:
            reasons.append(
                "position_size_invalid"
            )

        entry_price = float(
            position_size.get(
                "entry_price",
                record.get(
                    "entry_price",
                    0.0,
                ),
            )
        )

        stop_price = float(
            position_size.get(
                "stop_price",
                record.get(
                    "stop_price",
                    0.0,
                ),
            )
        )

        target_price = float(
            record.get(
                "target_price",
                0.0,
            )
        )

        if entry_price <= 0:
            reasons.append(
                "entry_price_invalid"
            )

        if (
            bool(
                self.requirements.get(
                    "require_stop_loss",
                    True,
                )
            )
            and stop_price <= 0
        ):
            reasons.append(
                "stop_price_missing"
            )

        if (
            bool(
                self.requirements.get(
                    "require_take_profit",
                    True,
                )
            )
            and target_price <= 0
        ):
            reasons.append(
                "target_price_missing"
            )

        if (
            side == "buy"
            and stop_price >= entry_price
        ):
            reasons.append(
                "buy_stop_not_below_entry"
            )

        if (
            side == "buy"
            and target_price <= entry_price
        ):
            reasons.append(
                "buy_target_not_above_entry"
            )

        if (
            side == "sell"
            and stop_price <= entry_price
        ):
            reasons.append(
                "sell_stop_not_above_entry"
            )

        if (
            side == "sell"
            and target_price >= entry_price
        ):
            reasons.append(
                "sell_target_not_below_entry"
            )

        return {
            "approved": len(
                reasons
            ) == 0,
            "instrument": instrument,
            "side": side,
            "reasons": reasons,
            "warnings": warnings,
            "candidate_age_minutes": (
                candidate_age
            ),
            "alliance_score": score,
            "confirming_experts": experts,
            "reward_risk": reward_risk,
            "units": units,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
        }
