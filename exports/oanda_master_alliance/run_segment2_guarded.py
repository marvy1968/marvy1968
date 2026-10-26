from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_foundation_snapshot import (
    main as refresh_foundation,
)


ROOT = Path(__file__).resolve().parent

MARKET_SNAPSHOT = (
    ROOT
    / "state"
    / "market_snapshot.json"
)

REGIME_SNAPSHOT = (
    ROOT
    / "state"
    / "regime_snapshot.json"
)

STATUS_FILE = (
    ROOT
    / "state"
    / "decisions"
    / "segment2_status.json"
)

DECISIONS_FILE = (
    ROOT
    / "state"
    / "decisions"
    / "latest_decisions.json"
)

SHORTLIST_FILE = (
    ROOT
    / "state"
    / "decisions"
    / "latest_shortlist.json"
)

EVENT_LOG = (
    ROOT
    / "logs"
    / "segment2_events.jsonl"
)


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_json(
    path: Path,
) -> Any:
    if not path.exists():
        return {}

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def atomic_json(
    path: Path,
    payload: Any,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(
        path
    )


def append_event(
    event: str,
    **fields: Any,
) -> None:
    EVENT_LOG.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    record = {
        "timestamp": utc_now(),
        "event": event,
        **fields,
    }

    with EVENT_LOG.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                record,
                default=str,
            )
            + "\n"
        )


def extract_quality_details(
    market: dict[str, Any],
    regimes: dict[str, Any],
) -> dict[str, Any]:
    market_regimes = market.get(
        "regimes",
        {},
    )

    detailed_regimes = regimes

    if (
        isinstance(regimes, dict)
        and isinstance(
            regimes.get("regimes"),
            dict,
        )
    ):
        detailed_regimes = regimes[
            "regimes"
        ]

    result: dict[str, Any] = {}

    symbols = sorted(
        set(market_regimes)
        | (
            set(detailed_regimes)
            if isinstance(
                detailed_regimes,
                dict,
            )
            else set()
        )
    )

    for symbol in symbols:
        market_record = (
            market_regimes.get(
                symbol,
                {},
            )
            if isinstance(
                market_regimes,
                dict,
            )
            else {}
        )

        detail = (
            detailed_regimes.get(
                symbol,
                {},
            )
            if isinstance(
                detailed_regimes,
                dict,
            )
            else {}
        )

        reasons = []

        for key in (
            "rejection_reasons",
            "data_quality_reasons",
            "quality_errors",
            "errors",
        ):
            value = detail.get(
                key,
                [],
            )

            if isinstance(
                value,
                str,
            ):
                value = [value]

            if isinstance(
                value,
                list,
            ):
                reasons.extend(
                    str(item)
                    for item in value
                )

        timeframe_failures = []

        for timeframe in detail.get(
            "timeframe_results",
            [],
        ):
            if not isinstance(
                timeframe,
                dict,
            ):
                continue

            if timeframe.get(
                "data_quality_passed",
                True,
            ) is False:
                timeframe_failures.append(
                    {
                        "timeframe": (
                            timeframe.get(
                                "timeframe"
                            )
                        ),
                        "reasons": (
                            timeframe.get(
                                "rejection_reasons",
                                timeframe.get(
                                    "quality_errors",
                                    [],
                                ),
                            )
                        ),
                    }
                )

        passed = bool(
            market_record.get(
                "data_quality",
                detail.get(
                    "data_quality_passed",
                    False,
                ),
            )
        )

        result[symbol] = {
            "passed": passed,
            "regime": (
                market_record.get(
                    "regime",
                    detail.get(
                        "regime"
                    ),
                )
            ),
            "direction": (
                market_record.get(
                    "direction",
                    detail.get(
                        "direction"
                    ),
                )
            ),
            "reasons": sorted(
                set(reasons)
            ),
            "timeframe_failures": (
                timeframe_failures
            ),
        }

    return result


def write_blocked_outputs(
    market: dict[str, Any],
    quality: dict[str, Any],
) -> None:
    failed = [
        symbol
        for symbol, record
        in quality.items()
        if not record.get(
            "passed",
            False,
        )
    ]

    timestamp = utc_now()

    status = {
        "timestamp": timestamp,
        "status": (
            "blocked_data_quality"
        ),
        "environment": market.get(
            "environment",
            "practice",
        ),
        "research_only": True,
        "automatic_execution": False,
        "foundation_quality_passed": False,
        "pairs_evaluated": len(
            quality
        ),
        "pairs_failed_quality": (
            failed
        ),
        "alliance_decisions": 0,
        "candidate_decisions": 0,
        "shortlist_count": 0,
        "quality_details": quality,
        "message": (
            "Alliance evaluation was blocked "
            "because the foundation data-quality "
            "gate did not pass."
        ),
    }

    atomic_json(
        STATUS_FILE,
        status,
    )

    atomic_json(
        DECISIONS_FILE,
        {
            "timestamp": timestamp,
            "status": (
                "blocked_data_quality"
            ),
            "research_only": True,
            "automatic_execution": False,
            "decisions": [],
            "quality_details": quality,
        },
    )

    atomic_json(
        SHORTLIST_FILE,
        {
            "timestamp": timestamp,
            "status": (
                "blocked_data_quality"
            ),
            "research_only": True,
            "automatic_execution": False,
            "shortlist": [],
        },
    )

    append_event(
        "segment2_blocked_data_quality",
        failed_pairs=failed,
    )


def normalize_engine_result(
    result: Any,
) -> Any:
    if hasattr(
        result,
        "to_dict",
    ):
        return result.to_dict()

    return result


def run_alliance() -> Any:
    from brain.alliance_engine import (
        AllianceEngine,
    )

    engine = AllianceEngine()
    result = engine.run()

    return normalize_engine_result(
        result
    )


def persist_alliance_result(
    result: Any,
    market: dict[str, Any],
) -> None:
    timestamp = utc_now()

    if isinstance(
        result,
        dict,
    ):
        decisions = result.get(
            "decisions",
            result.get(
                "alliance_decisions",
                [],
            ),
        )

        shortlist = result.get(
            "shortlist",
            result.get(
                "approved_shortlist",
                [],
            ),
        )

    elif isinstance(
        result,
        list,
    ):
        decisions = result
        shortlist = [
            item
            for item in result
            if isinstance(
                item,
                dict,
            )
            and item.get(
                "approved",
                item.get(
                    "candidate",
                    False,
                ),
            )
        ]

    else:
        decisions = []
        shortlist = []

    if not isinstance(
        decisions,
        list,
    ):
        decisions = []

    if not isinstance(
        shortlist,
        list,
    ):
        shortlist = []

    candidates = [
        item
        for item in decisions
        if isinstance(
            item,
            dict,
        )
        and (
            item.get(
                "candidate",
                False,
            )
            or item.get(
                "status"
            )
            in {
                "candidate",
                "approved",
                "shortlist",
            }
        )
    ]

    atomic_json(
        DECISIONS_FILE,
        {
            "timestamp": timestamp,
            "status": "completed",
            "research_only": True,
            "automatic_execution": False,
            "decisions": decisions,
            "raw_result": result,
        },
    )

    atomic_json(
        SHORTLIST_FILE,
        {
            "timestamp": timestamp,
            "status": "completed",
            "research_only": True,
            "automatic_execution": False,
            "shortlist": shortlist,
        },
    )

    atomic_json(
        STATUS_FILE,
        {
            "timestamp": timestamp,
            "status": "completed",
            "environment": market.get(
                "environment",
                "practice",
            ),
            "research_only": True,
            "automatic_execution": False,
            "foundation_quality_passed": True,
            "alliance_decisions": len(
                decisions
            ),
            "candidate_decisions": len(
                candidates
            ),
            "shortlist_count": len(
                shortlist
            ),
            "last_error": None,
        },
    )

    append_event(
        "segment2_completed",
        decisions=len(
            decisions
        ),
        candidates=len(
            candidates
        ),
        shortlist=len(
            shortlist
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Guarded OANDA Master AI "
            "Segment 2 runner"
        )
    )

    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help=(
            "Use the current foundation "
            "snapshot instead of refreshing."
        ),
    )

    arguments = parser.parse_args()

    if not arguments.no_refresh:
        print(
            "Refreshing Segment 1 foundation..."
        )
        refresh_foundation()

    market = load_json(
        MARKET_SNAPSHOT
    )

    regimes = load_json(
        REGIME_SNAPSHOT
    )

    if not market:
        raise SystemExit(
            "Foundation market snapshot "
            "was not created."
        )

    quality = extract_quality_details(
        market,
        regimes,
    )

    quality_passed = bool(
        market.get(
            "data_quality_passed",
            False,
        )
    )

    if not quality_passed:
        write_blocked_outputs(
            market,
            quality,
        )

        print()
        print(
            "SEGMENT 2 BLOCKED BY "
            "DATA-QUALITY GATE"
        )
        print("=" * 72)

        for symbol, record in (
            quality.items()
        ):
            state = (
                "PASS"
                if record["passed"]
                else "FAIL"
            )

            print(
                f"{symbol:10} {state:4} "
                f"{record.get('regime')} "
                f"{record.get('direction')}"
            )

            for reason in record.get(
                "reasons",
                [],
            ):
                print(
                    f"  - {reason}"
                )

            for failure in record.get(
                "timeframe_failures",
                [],
            ):
                print(
                    "  - "
                    f"{failure.get('timeframe')}: "
                    f"{failure.get('reasons')}"
                )

        print()
        print(
            "No expert decisions were produced."
        )
        print(
            "No orders were enabled or placed."
        )
        return

    print(
        "Foundation quality passed. "
        "Running Alliance Brain..."
    )

    try:
        result = run_alliance()

        persist_alliance_result(
            result,
            market,
        )

        print()
        print(
            "Segment 2 Alliance Brain completed."
        )

    except Exception as exc:
        error = (
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        atomic_json(
            STATUS_FILE,
            {
                "timestamp": utc_now(),
                "status": "error",
                "research_only": True,
                "automatic_execution": False,
                "last_error": error,
            },
        )

        append_event(
            "segment2_error",
            error=error,
            traceback=traceback.format_exc(),
        )

        raise


if __name__ == "__main__":
    main()
