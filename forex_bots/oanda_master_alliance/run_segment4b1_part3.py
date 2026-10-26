from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.common import ROOT
from segment4b.activation_gate import (
    PracticeExecutionGate,
)
from segment4b.candidate_validator import (
    CandidateValidator,
)
from segment4b.common import (
    append_jsonl,
    atomic_write_json,
    load_segment4b_config,
    read_json,
    storage_path,
    utc_now_iso,
)
from segment4b.execution_counters import (
    ExecutionCounterManager,
)
from segment4b.fill_verifier import (
    SimulatedFillVerifier,
)
from segment4b.market_validator import (
    MarketConditionValidator,
)
from segment4b.models import (
    OrderIntent,
)
from segment4b.order_intent import (
    OrderIntentBuilder,
)
from segment4b.payload_builder import (
    DryRunPayloadBuilder,
)
from segment4b.reconciliation import (
    BrokerStateReconciler,
)
from segment4b.rejection_recorder import (
    RejectionRecorder,
)
from segment4b.simulated_router import (
    SimulatedPracticeRouter,
)


def load_segment3_status() -> dict[str, Any]:
    paths = [
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

    for path in paths:
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

    return {}


def reconstruct_intent(
    payload: dict[str, Any],
) -> OrderIntent:
    allowed_fields = {
        "timestamp",
        "intent_id",
        "instrument",
        "side",
        "units",
        "signed_units",
        "order_type",
        "time_in_force",
        "position_fill",
        "entry_price",
        "stop_price",
        "target_price",
        "reward_risk",
        "alliance_score",
        "confirming_experts",
        "authorization_id",
        "client_extension_id",
        "source_timestamp",
        "mode",
        "metadata",
    }

    filtered = {
        key: value
        for key, value in payload.items()
        if key in allowed_fields
    }

    return OrderIntent(
        **filtered
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--skip-live-pricing",
        action="store_true",
    )

    parser.add_argument(
        "--skip-broker-reconciliation",
        action="store_true",
    )

    parser.add_argument(
        "--reset-simulation-counters",
        action="store_true",
    )

    arguments = parser.parse_args()

    config = load_segment4b_config()

    if arguments.reset_simulation_counters:
        storage_path(
            config,
            "hourly_counter",
        ).unlink(
            missing_ok=True
        )

        storage_path(
            config,
            "daily_counter",
        ).unlink(
            missing_ok=True
        )

    gate = PracticeExecutionGate(
        config
    ).evaluate()

    if arguments.skip_broker_reconciliation:
        reconciliation = {
            "timestamp": utc_now_iso(),
            "passed": True,
            "skipped": True,
            "issues": [],
            "automatic_execution": False,
            "order_submission": False,
        }

    else:
        reconciliation = (
            BrokerStateReconciler(
                config
            ).run().to_dict()
        )

    segment3 = load_segment3_status()

    raw_results = segment3.get(
        "results",
        [],
    )

    records = (
        raw_results
        if isinstance(
            raw_results,
            list,
        )
        else []
    )

    validator = CandidateValidator(
        config
    )

    intent_builder = OrderIntentBuilder(
        config
    )

    payload_builder = (
        DryRunPayloadBuilder()
    )

    counters = ExecutionCounterManager(
        config
    )

    simulated_router = (
        SimulatedPracticeRouter(
            config
        )
    )

    fill_verifier = (
        SimulatedFillVerifier(
            config
        )
    )

    rejection_recorder = (
        RejectionRecorder(
            config
        )
    )

    live_validator = (
        None
        if arguments.skip_live_pricing
        else MarketConditionValidator(
            config
        )
    )

    evaluations: list[
        dict[str, Any]
    ] = []

    simulated_count = 0

    for record in records:
        if not isinstance(
            record,
            dict,
        ):
            continue

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

        evaluation: dict[str, Any] = {
            "instrument": instrument,
            "side": side,
            "candidate_validation": None,
            "reconciliation_passed": bool(
                reconciliation.get(
                    "passed",
                    False,
                )
            ),
            "market_validation": None,
            "counter_validation": None,
            "intent": None,
            "payload": None,
            "receipt": None,
            "fill_verification": None,
            "rejected": False,
            "rejection_reasons": [],
        }

        candidate_validation = (
            validator.validate(
                record
            )
        )

        evaluation[
            "candidate_validation"
        ] = candidate_validation

        if not candidate_validation.get(
            "approved",
            False,
        ):
            reasons = list(
                candidate_validation.get(
                    "reasons",
                    [],
                )
            )

            evaluation[
                "rejected"
            ] = True

            evaluation[
                "rejection_reasons"
            ].extend(
                reasons
            )

            rejection_recorder.record(
                stage="candidate_validation",
                instrument=instrument,
                side=side,
                reasons=reasons,
            )

            evaluations.append(
                evaluation
            )

            continue

        if not reconciliation.get(
            "passed",
            False,
        ):
            reasons = [
                "broker_reconciliation_failed"
            ]

            evaluation[
                "rejected"
            ] = True

            evaluation[
                "rejection_reasons"
            ].extend(
                reasons
            )

            rejection_recorder.record(
                stage="broker_reconciliation",
                instrument=instrument,
                side=side,
                reasons=reasons,
                metadata={
                    "reconciliation": (
                        reconciliation
                    )
                },
            )

            evaluations.append(
                evaluation
            )

            continue

        try:
            intent = intent_builder.build(
                record
            )

        except Exception as error:
            reasons = [
                (
                    "intent_build_failed:"
                    f"{type(error).__name__}"
                )
            ]

            evaluation[
                "rejected"
            ] = True

            evaluation[
                "rejection_reasons"
            ].extend(
                reasons
            )

            rejection_recorder.record(
                stage="intent_build",
                instrument=instrument,
                side=side,
                reasons=reasons,
                metadata={
                    "error": str(
                        error
                    )
                },
            )

            evaluations.append(
                evaluation
            )

            continue

        payload = payload_builder.build(
            intent
        )

        evaluation[
            "intent"
        ] = intent.to_dict()

        evaluation[
            "payload"
        ] = payload

        if live_validator is None:
            market_validation = {
                "timestamp": utc_now_iso(),
                "passed": True,
                "skipped": True,
                "reasons": [],
                "warnings": [],
                "automatic_execution": False,
                "order_submission": False,
            }

        else:
            try:
                market_validation = (
                    live_validator.validate(
                        intent
                    ).to_dict()
                )

            except Exception as error:
                market_validation = {
                    "timestamp": (
                        utc_now_iso()
                    ),
                    "passed": False,
                    "skipped": False,
                    "reasons": [
                        (
                            "pricing_validation_failed:"
                            f"{type(error).__name__}"
                        )
                    ],
                    "warnings": [],
                    "error": str(
                        error
                    ),
                    "automatic_execution": False,
                    "order_submission": False,
                }

        evaluation[
            "market_validation"
        ] = market_validation

        if not market_validation.get(
            "passed",
            False,
        ):
            reasons = list(
                market_validation.get(
                    "reasons",
                    [],
                )
            )

            evaluation[
                "rejected"
            ] = True

            evaluation[
                "rejection_reasons"
            ].extend(
                reasons
            )

            rejection_recorder.record(
                stage="market_validation",
                instrument=instrument,
                side=side,
                reasons=reasons,
                metadata={
                    "market_validation": (
                        market_validation
                    )
                },
            )

            evaluations.append(
                evaluation
            )

            continue

        counter_validation = (
            counters.evaluate(
                cycle_count=(
                    simulated_count
                )
            )
        )

        evaluation[
            "counter_validation"
        ] = counter_validation

        if not counter_validation.get(
            "passed",
            False,
        ):
            reasons = list(
                counter_validation.get(
                    "reasons",
                    [],
                )
            )

            evaluation[
                "rejected"
            ] = True

            evaluation[
                "rejection_reasons"
            ].extend(
                reasons
            )

            rejection_recorder.record(
                stage="rate_limit",
                instrument=instrument,
                side=side,
                reasons=reasons,
                metadata={
                    "counter_validation": (
                        counter_validation
                    )
                },
            )

            evaluations.append(
                evaluation
            )

            continue

        try:
            receipt = (
                simulated_router.simulate(
                    intent=intent,
                    payload=payload,
                )
            )

        except Exception as error:
            reasons = [
                str(
                    error
                )
            ]

            evaluation[
                "rejected"
            ] = True

            evaluation[
                "rejection_reasons"
            ].extend(
                reasons
            )

            rejection_recorder.record(
                stage="duplicate_protection",
                instrument=instrument,
                side=side,
                reasons=reasons,
                metadata={
                    "intent_id": (
                        intent.intent_id
                    )
                },
            )

            evaluations.append(
                evaluation
            )

            continue

        evaluation[
            "receipt"
        ] = receipt.to_dict()

        fill_result = (
            fill_verifier.verify(
                intent=intent,
                receipt=receipt,
                payload=payload,
            )
        )

        evaluation[
            "fill_verification"
        ] = fill_result.to_dict()

        counters.record_simulation(
            intent_id=(
                intent.intent_id
            ),
            instrument=(
                intent.instrument
            ),
            side=intent.side,
        )

        simulated_count += 1

        intent_path = (
            ROOT
            / "state/execution4b/"
            "intents"
            / (
                f"{intent.intent_id}.json"
            )
        )

        payload_path = (
            ROOT
            / "state/execution4b/"
            "payloads"
            / (
                f"{intent.intent_id}.json"
            )
        )

        atomic_write_json(
            intent_path,
            intent.to_dict(),
        )

        atomic_write_json(
            payload_path,
            {
                "timestamp": (
                    utc_now_iso()
                ),
                "intent_id": (
                    intent.intent_id
                ),
                "mode": (
                    "simulation_only"
                ),
                "submitted": False,
                "payload": payload,
            },
        )

        evaluations.append(
            evaluation
        )

    status = {
        "timestamp": utc_now_iso(),
        "segment": "4B-1-part3",
        "environment": "practice",
        "mode": (
            "simulation_only"
        ),
        "execution_gate_allowed": (
            gate.allowed
        ),
        "execution_gate_reasons": (
            gate.reasons
        ),
        "reconciliation": (
            reconciliation
        ),
        "segment3_candidates": len(
            records
        ),
        "evaluated_candidates": len(
            evaluations
        ),
        "simulated_acceptances": (
            simulated_count
        ),
        "verified_simulated_fills": sum(
            1
            for item in evaluations
            if isinstance(
                item.get(
                    "fill_verification"
                ),
                dict,
            )
            and bool(
                item[
                    "fill_verification"
                ].get(
                    "passed",
                    False,
                )
            )
        ),
        "rejected_candidates": sum(
            1
            for item in evaluations
            if item.get(
                "rejected",
                False,
            )
        ),
        "orders_submitted": 0,
        "automatic_execution": False,
        (
            "practice_order_"
            "submission_enabled"
        ): False,
        "counter_snapshot": (
            counters.snapshot()
        ),
        "evaluations": evaluations,
    }

    atomic_write_json(
        storage_path(
            config,
            "execution_status",
        ),
        status,
    )

    append_jsonl(
        storage_path(
            config,
            "authorization_history",
        ),
        status,
    )

    print(
        json.dumps(
            status,
            indent=2,
        )
    )

    print()
    print(
        "Segment 4B-1 Part 3 simulation completed."
    )
    print(
        "No OANDA order endpoint was called."
    )
    print(
        "Orders submitted: 0"
    )


if __name__ == "__main__":
    main()
