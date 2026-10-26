from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from segment4b4.common import (
    ROOT,
    read_json,
    utc_now_iso,
)
from segment4b4.models import (
    CandidateDecision,
    CandidateLoadResult,
)


class CandidateLoader:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

        self.source_path = (
            ROOT
            / "state/execution/"
            "segment3_latest.json"
        )

        alternate = (
            ROOT
            / "state/execution/"
            "segment3_status.json"
        )

        if not self.source_path.exists():
            self.source_path = alternate

    @staticmethod
    def _age_minutes(
        timestamp: str,
    ) -> float:
        if not timestamp:
            return float("inf")

        value = timestamp.replace(
            "Z",
            "+00:00",
        )

        parsed = datetime.fromisoformat(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        delta = (
            datetime.now(timezone.utc)
            - parsed.astimezone(timezone.utc)
        )

        return max(
            0.0,
            delta.total_seconds() / 60.0,
        )

    def load(self) -> CandidateLoadResult:
        if not self.source_path.exists():
            return CandidateLoadResult(
                timestamp=utc_now_iso(),
                source_exists=False,
                loaded=0,
                approved=0,
                rejected=0,
                candidates=[],
                rejection_reasons=[],
            )

        payload = read_json(
            self.source_path,
            default={},
        )

        records = payload.get(
            "results",
            [],
        )

        if not isinstance(records, list):
            records = []

        settings = self.config.get(
            "candidate_capture",
            {},
        )

        maximum_age = float(
            settings.get(
                "maximum_candidate_age_minutes",
                20,
            )
        )

        minimum_rr = float(
            settings.get(
                "minimum_reward_risk",
                1.5,
            )
        )

        candidates: list[CandidateDecision] = []
        rejections: list[dict[str, Any]] = []

        for record in records:
            if not isinstance(record, dict):
                rejections.append(
                    {
                        "instrument": "",
                        "reasons": [
                            "malformed_candidate"
                        ],
                    }
                )
                continue

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

            risk = record.get(
                "risk",
                {},
            )

            authorization = record.get(
                "authorization",
                {},
            )

            position_size = (
                risk.get(
                    "position_size",
                    {},
                )
                if isinstance(risk, dict)
                else {}
            )

            risk_approved = bool(
                risk.get(
                    "approved",
                    False,
                )
                if isinstance(risk, dict)
                else False
            )

            authorized = bool(
                authorization.get(
                    "authorized",
                    False,
                )
                if isinstance(
                    authorization,
                    dict,
                )
                else False
            )

            source_timestamp = str(
                authorization.get(
                    "timestamp",
                    payload.get(
                        "timestamp",
                        "",
                    ),
                )
                if isinstance(
                    authorization,
                    dict,
                )
                else payload.get(
                    "timestamp",
                    "",
                )
            )

            reward_risk = float(
                record.get(
                    "reward_risk",
                    0.0,
                )
            )

            entry_price = float(
                position_size.get(
                    "entry_price",
                    record.get(
                        "entry_price",
                        0.0,
                    ),
                )
                if isinstance(
                    position_size,
                    dict,
                )
                else 0.0
            )

            stop_price = float(
                position_size.get(
                    "stop_price",
                    record.get(
                        "stop_price",
                        0.0,
                    ),
                )
                if isinstance(
                    position_size,
                    dict,
                )
                else 0.0
            )

            target_price = float(
                record.get(
                    "target_price",
                    0.0,
                )
            )

            units = int(
                position_size.get(
                    "units",
                    0,
                )
                if isinstance(
                    position_size,
                    dict,
                )
                else 0
            )

            if not instrument:
                reasons.append(
                    "instrument_missing"
                )

            if side not in {
                "buy",
                "sell",
            }:
                reasons.append(
                    "invalid_side"
                )

            if not risk_approved:
                reasons.append(
                    "risk_not_approved"
                )

            if not authorized:
                reasons.append(
                    "authorization_missing"
                )

            if units <= 0:
                reasons.append(
                    "invalid_units"
                )

            if entry_price <= 0:
                reasons.append(
                    "entry_price_missing"
                )

            if stop_price <= 0:
                reasons.append(
                    "stop_price_missing"
                )

            if target_price <= 0:
                reasons.append(
                    "target_price_missing"
                )

            if reward_risk < minimum_rr:
                reasons.append(
                    "reward_risk_below_minimum"
                )

            if (
                self._age_minutes(
                    source_timestamp
                )
                > maximum_age
            ):
                reasons.append(
                    "candidate_stale"
                )

            if reasons:
                rejections.append(
                    {
                        "instrument": instrument,
                        "side": side,
                        "reasons": reasons,
                    }
                )
                continue

            expert_names: list[str] = []
            setup_names: list[str] = []

            expert_signals = record.get(
                "expert_signals",
                [],
            )

            if isinstance(
                expert_signals,
                list,
            ):
                for signal in expert_signals:
                    if not isinstance(
                        signal,
                        dict,
                    ):
                        continue

                    if signal.get(
                        "status"
                    ) != "candidate":
                        continue

                    expert = str(
                        signal.get(
                            "expert",
                            "",
                        )
                    ).strip()

                    setup = str(
                        signal.get(
                            "setup",
                            "",
                        )
                    ).strip()

                    if expert:
                        expert_names.append(
                            expert
                        )

                    if setup:
                        setup_names.append(
                            setup
                        )

            candidates.append(
                CandidateDecision(
                    instrument=instrument,
                    side=side,
                    alliance_score=float(
                        record.get(
                            "alliance_score",
                            0.0,
                        )
                    ),
                    confirming_experts=int(
                        record.get(
                            "confirming_experts",
                            0,
                        )
                    ),
                    reward_risk=reward_risk,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    units=units,
                    authorization_id=str(
                        authorization.get(
                            "authorization_id",
                            "",
                        )
                    ),
                    source_timestamp=(
                        source_timestamp
                    ),
                    expert_names=sorted(
                        set(expert_names)
                    ),
                    setup_names=sorted(
                        set(setup_names)
                    ),
                    metadata={
                        "source_file": str(
                            self.source_path
                        ),
                        "automatic_execution": (
                            False
                        ),
                        "order_submission": (
                            False
                        ),
                    },
                )
            )

        return CandidateLoadResult(
            timestamp=utc_now_iso(),
            source_exists=True,
            loaded=len(records),
            approved=len(candidates),
            rejected=len(rejections),
            candidates=candidates,
            rejection_reasons=rejections,
        )
