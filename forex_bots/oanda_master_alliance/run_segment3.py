from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from broker.oanda_v20 import OandaV20Client
from core.common import ROOT
from execution.authorization import (
    ExecutionAuthorization,
)
from execution.order_router import (
    OrderRouter,
)
from risk.risk_engine import RiskEngine


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_segment3_config() -> dict[str, Any]:
    path = ROOT / "config/segment3.yaml"

    return yaml.safe_load(
        path.read_text(
            encoding="utf-8"
        )
    )


def load_json(
    path: Path,
    default: Any,
) -> Any:
    if not path.exists():
        return default

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def write_json(
    path: Path,
    payload: Any,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def shortlist_age_minutes(
    payload: dict[str, Any],
) -> float:
    timestamp = datetime.fromisoformat(
        str(payload["timestamp"])
    )

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(
            tzinfo=timezone.utc
        )

    return (
        utc_now() - timestamp
    ).total_seconds() / 60.0


def build_client():
    """
    Construct the installed OANDA client while supporting constructor
    names used by earlier OANDA Master Alliance segments.

    This changes no environment or execution setting.
    """
    import inspect
    import os

    from dotenv import load_dotenv

    load_dotenv(
        ROOT / ".env",
        override=True,
    )

    token = os.getenv(
        "OANDA_API_TOKEN",
        "",
    ).strip()

    account_id = os.getenv(
        "OANDA_ACCOUNT_ID",
        "",
    ).strip()

    environment = os.getenv(
        "OANDA_ENV",
        "practice",
    ).strip().lower()

    if not token:
        raise RuntimeError(
            "OANDA_API_TOKEN is missing."
        )

    if not account_id:
        raise RuntimeError(
            "OANDA_ACCOUNT_ID is missing."
        )

    if environment not in {
        "practice",
        "live",
    }:
        raise RuntimeError(
            "OANDA_ENV must be practice or live."
        )

    if environment != "practice":
        raise RuntimeError(
            "Segment 3 is practice-only. "
            "Live environment is blocked."
        )

    signature = inspect.signature(
        OandaV20Client.__init__
    )

    parameters = signature.parameters

    keyword_arguments = {}

    token_names = (
        "api_token",
        "token",
        "access_token",
        "api_key",
    )

    account_names = (
        "account_id",
        "account",
        "oanda_account_id",
    )

    environment_names = (
        "environment",
        "env",
        "oanda_env",
    )

    timeout_names = (
        "timeout",
        "request_timeout",
        "request_timeout_seconds",
    )

    for name in token_names:
        if name in parameters:
            keyword_arguments[name] = token
            break

    for name in account_names:
        if name in parameters:
            keyword_arguments[name] = account_id
            break

    for name in environment_names:
        if name in parameters:
            keyword_arguments[name] = environment
            break

    for name in timeout_names:
        if name in parameters:
            keyword_arguments[name] = 30
            break

    missing_required = []

    for name, parameter in parameters.items():
        if name == "self":
            continue

        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue

        if parameter.default is not inspect.Parameter.empty:
            continue

        if name not in keyword_arguments:
            missing_required.append(name)

    if not missing_required:
        return OandaV20Client(
            **keyword_arguments
        )

    # Positional fallback for older client implementations whose first
    # required arguments are token and account ID.
    positional_values = []

    for name in missing_required:
        lowered = name.lower()

        if lowered in token_names:
            positional_values.append(token)

        elif lowered in account_names:
            positional_values.append(account_id)

        elif lowered in environment_names:
            positional_values.append(environment)

        else:
            raise RuntimeError(
                "Unable to construct OANDA client. "
                f"Unsupported required parameter: {name}. "
                f"Constructor signature: {signature}"
            )

    return OandaV20Client(
        *positional_values,
        **keyword_arguments,
    )


def account_summary(
    client: OandaV20Client,
) -> dict[str, Any]:
    response = client.account_summary()

    if "account" in response:
        return dict(response["account"])

    return dict(response)



def refresh_segment2_shortlist() -> None:
    """
    Refresh Segment 1 market intelligence and rebuild the Segment 2
    shortlist before Segment 3 risk authorization.

    This function does not place orders.
    """
    from run_segment2 import main as run_segment2_main

    print(
        "Refreshing Segment 1 and Segment 2 "
        "before risk authorization..."
    )

    try:
        run_segment2_main(
            refresh_foundation=True
        )
    except TypeError:
        # Support the current command-line-style Segment 2 runner.
        import subprocess
        import sys

        subprocess.run(
            [
                sys.executable,
                "run_segment2.py",
                "--refresh-foundation",
            ],
            check=True,
        )


def main() -> None:
    refresh_segment2_shortlist()
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI Segment 3"
        )
    )

    parser.add_argument(
        "--submit-practice-orders",
        action="store_true",
        help=(
            "Request practice-order submission. "
            "Configuration authorization is still required."
        ),
    )

    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=5,
    )

    arguments = parser.parse_args()

    config = load_segment3_config()
    segment3 = config["segment3"]
    files = segment3["files"]

    shortlist_path = (
        ROOT
        / files["segment2_shortlist"]
    )

    if not shortlist_path.exists():
        raise SystemExit(
            "Segment 2 shortlist has not been created."
        )

    shortlist_payload = load_json(
        shortlist_path,
        {},
    )

    age_minutes = shortlist_age_minutes(
        shortlist_payload
    )

    if (
        bool(
            segment3[
                "require_fresh_shortlist"
            ]
        )
        and age_minutes
        > float(
            segment3[
                "maximum_shortlist_age_minutes"
            ]
        )
    ):
        raise SystemExit(
            "Segment 2 shortlist is stale: "
            f"{age_minutes:.1f} minutes old."
        )

    client = build_client()
    account = account_summary(
        client
    )

    runtime_path = (
        ROOT
        / files["runtime_status"]
    )

    runtime = load_json(
        runtime_path,
        {
            "realized_daily_r": 0.0,
            "realized_weekly_r": 0.0,
            "consecutive_losses": 0,
            "daily_trades": 0,
            "open_positions": 0,
        },
    )

    risk_engine = RiskEngine(
        config
    )

    authorization_engine = (
        ExecutionAuthorization(
            config
        )
    )

    router = OrderRouter(
        client=client,
        config=config,
    )

    results: list[dict[str, Any]] = []

    shortlist = list(
        shortlist_payload.get(
            "shortlist",
            [],
        )
    )[
        : max(
            0,
            arguments.candidate_limit,
        )
    ]

    for candidate in shortlist:
        risk_decision = (
            risk_engine.evaluate(
                candidate=candidate,
                account=account,
                runtime=runtime,
            )
        )

        authorization = (
            authorization_engine.evaluate(
                candidate=candidate,
                risk_approved=(
                    risk_decision.approved
                ),
                submit_requested=(
                    arguments
                    .submit_practice_orders
                ),
            )
        )

        record: dict[str, Any] = {
            "instrument": (
                candidate.get(
                    "instrument"
                )
            ),
            "side": candidate.get("side"),
            "alliance_score": (
                candidate.get(
                    "alliance_score"
                )
            ),
            "risk": (
                risk_decision.to_dict()
            ),
            "authorization": (
                authorization.to_dict()
            ),
            "order": None,
        }

        if authorization.authorized:
            if (
                arguments
                .submit_practice_orders
            ):
                if (
                    risk_decision
                    .position_size
                    is None
                ):
                    raise RuntimeError(
                        "Authorized order has "
                        "no position size."
                    )

                order = router.submit(
                    candidate=candidate,
                    signed_units=(
                        risk_decision
                        .position_size
                        .signed_units
                    ),
                    authorization_id=(
                        authorization
                        .authorization_id
                    ),
                )

                authorization_engine.mark_processed(
                    authorization
                    .authorization_id
                )

                record["order"] = order

            else:
                record["order"] = {
                    "status": (
                        "research_preview"
                    ),
                    "message": (
                        "Risk approved. "
                        "No order submitted."
                    ),
                }

        results.append(record)

    approved = sum(
        1
        for item in results
        if item["risk"]["approved"]
    )

    submitted = sum(
        1
        for item in results
        if item.get("order")
        and item["order"].get(
            "status"
        )
        == "submitted"
    )

    status = {
        "timestamp": utc_now().isoformat(),
        "environment": (
            segment3["environment"]
        ),
        "practice_only": (
            segment3["practice_only"]
        ),
        "automatic_execution": (
            segment3[
                "automatic_execution"
            ]
        ),
        "practice_order_submission_enabled": (
            segment3[
                "practice_order_submission_enabled"
            ]
        ),
        "submit_requested": (
            arguments
            .submit_practice_orders
        ),
        "shortlist_age_minutes": (
            age_minutes
        ),
        "evaluated_candidates": len(
            results
        ),
        "risk_approved_candidates": (
            approved
        ),
        "submitted_orders": submitted,
        "realized_daily_r": float(
            runtime.get(
                "realized_daily_r",
                0.0,
            )
        ),
        "realized_weekly_r": float(
            runtime.get(
                "realized_weekly_r",
                0.0,
            )
        ),
        "consecutive_losses": int(
            runtime.get(
                "consecutive_losses",
                0,
            )
        ),
        "results": results,
    }

    write_json(
        runtime_path,
        status,
    )

    print(
        json.dumps(
            status,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
