from __future__ import annotations

import json
from pathlib import Path

from expert_library.library import (
    ExpertLibrary,
)


def main() -> None:
    library = ExpertLibrary()

    errors = library.validate()

    print()
    print(
        "OANDA EXPERT LIBRARY "
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

    coverage = library.coverage()

    print(
        "Status:                 PASSED"
    )
    print(
        "Experts:               "
        f"{coverage['experts']}"
    )
    print(
        "Sources registered:    "
        f"{coverage['sources_registered']}"
    )
    print(
        "Sources reviewed:      "
        f"{coverage['sources_reviewed']}"
    )
    print(
        "Doctrine rules:        "
        f"{coverage['doctrine_rules']}"
    )
    print(
        "Mechanical rules:      "
        f"{coverage['mechanical_rules']}"
    )
    print(
        "Research strategies:   "
        f"{coverage['strategies']}"
    )
    print(
        "Production enabled:    "
        f"{coverage['production_enabled']}"
    )

    output = Path(
        "reports/expert_library"
    )

    required_files = [
        output
        / "expert_library.json",
        output
        / "expert_library_report.md",
        output
        / "research_queue.csv",
    ]

    print()
    print(
        "OUTPUT FILES"
    )
    print("=" * 72)

    for path in required_files:
        print(
            f"{path}: "
            f"{'present' if path.exists() else 'missing'}"
        )

        if not path.exists():
            raise SystemExit(1)

    data = json.loads(
        (
            output
            / "expert_library.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert (
        data["metadata"][
            "research_only"
        ]
        is True
    )

    assert (
        data["metadata"][
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
