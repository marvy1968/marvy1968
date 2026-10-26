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
from segment4b.order_intent import (
    OrderIntentBuilder,
)
from segment4b.payload_builder import (
    DryRunPayloadBuilder,
)
from segment4b.reconciliation import (
    BrokerStateReconciler,
)


def find_segment3_results() -> dict[str, Any]:
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

    raise FileNotFoundError(
        "No Segment 3 result file was found."
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--skip-broker",
        action="store_true",
        help=(
            "Skip live read-only broker reconciliation. "
            "Used only for local verification."
        ),
    )

    arguments = parser.parse_args()

    config = load_segment4b_config()

    gate = PracticeExecutionGate(
        config
    ).evaluate()

    reconciliation: dict[str, Any]

    if arguments.skip_broker:
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

    segment3 = find_segment3_results()

    raw_results = segment3.get(
        "results",
        [],
    )

    results = (
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

    evaluations: list[
        dict[str, Any]
    ] = []

    for record in results:
        if not isinstance(
            record,
            dict,
        ):
            continue

        validation = validator.validate(
            record
        )

        evaluation: dict[str, Any] = {
            "instrument": (
                validation[
                    "instrument"
                ]
            ),
            "side": validation[
                "side"
            ],
            "validation": validation,
            "intent": None,
            "dry_run_payload": None,
        }

        if (
            validation[
                "approved"
            ]
            and reconciliation.get(
                "passed",
                False,
            )
        ):
            try:
                intent = intent_builder.build(
                    record
                )

                payload = (
                    payload_builder.build(
                        intent
                    )
                )

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
                        "mode": "dry_run",
                        "submitted": False,
                        "payload": payload,
                    },
                )

                evaluation[
                    "intent"
                ] = intent.to_dict()

                evaluation[
                    "dry_run_payload"
                ] = payload

            except Exception as error:
                validation[
                    "approved"
                ] = False

                validation[
                    "reasons"
                ].append(
                    (
                        "intent_build_failed:"
                        f"{type(error).__name__}"
                    )
                )

                evaluation[
                    "error"
                ] = str(
                    error
                )

        evaluations.append(
            evaluation
        )

    approved_intents = sum(
        1
        for item in evaluations
        if item.get(
            "intent"
        )
    )

    status = {
        "timestamp": utc_now_iso(),
        "segment": "4B-1-part2",
        "environment": "practice",
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
            results
        ),
        "validated_candidates": sum(
            1
            for item in evaluations
            if item[
                "validation"
            ][
                "approved"
            ]
        ),
        "dry_run_intents_created": (
            approved_intents
        ),
        "orders_submitted": 0,
        "automatic_execution": False,
        (
            "practice_order_"
            "submission_enabled"
        ): False,
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
        "Segment 4B-1 Part 2 completed in dry-run mode."
    )
    print(
        "No OANDA order endpoint was called."
    )


if __name__ == "__main__":
    main()
