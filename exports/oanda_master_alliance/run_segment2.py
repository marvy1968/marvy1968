from __future__ import annotations

import argparse
import json
from pathlib import Path

from brain.alliance_engine import (
    AllianceEngine,
)
from core.common import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI Segment 2 "
            "research signal cycle"
        )
    )

    parser.add_argument(
        "--refresh-foundation",
        action="store_true",
        help=(
            "Run Segment 1 foundation snapshot "
            "before evaluating experts."
        ),
    )

    arguments = parser.parse_args()

    if arguments.refresh_foundation:
        from run_foundation_snapshot import (
            main as run_foundation,
        )

        run_foundation()

    engine = AllianceEngine()
    result = engine.run()

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    shortlist_path = (
        ROOT
        / "state"
        / "decisions"
        / "latest_shortlist.json"
    )

    if shortlist_path.exists():
        shortlist = json.loads(
            shortlist_path.read_text(
                encoding="utf-8"
            )
        )

        print()
        print(
            "MASTER AI SHORTLIST"
        )
        print("=" * 88)

        items = shortlist.get(
            "shortlist",
            [],
        )

        if not items:
            print(
                "No trade candidates passed "
                "all Segment 2 gates."
            )

        for item in items:
            print(
                f"{item['instrument']:8} "
                f"{item['side'].upper():4} "
                f"score={item['alliance_score']:6.2f} "
                f"experts={item['confirming_experts']} "
                f"RR={item['reward_risk']:.2f} "
                f"decision={item['decision']}"
            )

    print()
    print(
        "Research only. "
        "No order was submitted."
    )


if __name__ == "__main__":
    main()
