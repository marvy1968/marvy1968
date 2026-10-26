from __future__ import annotations

import json
from pathlib import Path

from expert_library.research.review_engine import (
    REPORT_FILE,
    SUMMARY_FILE,
    SourceReviewEngine,
)


def main() -> None:
    engine = SourceReviewEngine()

    errors = engine.validate_storage()

    print()
    print(
        "OANDA SOURCE REVIEW ENGINE "
        "VERIFICATION"
    )
    print("=" * 72)

    if errors:
        print(
            "Status: FAILED"
        )

        for error in errors:
            print(
                f"- {error}"
            )

        raise SystemExit(1)

    summary = engine.rebuild_summary()

    print(
        "Status:                 PASSED"
    )
    print(
        "Reviews:                "
        f"{summary['reviews']}"
    )
    print(
        "Completed reviews:      "
        f"{summary['completed_reviews']}"
    )
    print(
        "Findings:               "
        f"{summary['findings']}"
    )
    print(
        "Open contradictions:    "
        f"{summary['open_contradictions']}"
    )
    print(
        "Promotion candidates:   "
        f"{summary['current_promotion_evaluation']['promote']}"
    )
    print(
        "Automatic trading:      "
        f"{summary['automatic_trading_enabled']}"
    )

    for path in [
        SUMMARY_FILE,
        REPORT_FILE,
    ]:
        if not path.exists():
            raise SystemExit(
                f"Missing output: {path}"
            )

        print(
            f"{path}: present"
        )

    payload = json.loads(
        Path(
            SUMMARY_FILE
        ).read_text(
            encoding="utf-8"
        )
    )

    assert (
        payload[
            "research_only"
        ]
        is True
    )

    assert (
        payload[
            "automatic_trading_enabled"
        ]
        is False
    )

    print()
    print(
        "Research-only guardrails passed."
    )


if __name__ == "__main__":
    main()
