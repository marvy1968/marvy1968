from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


FILES = {
    "signals": (
        PROJECT_ROOT
        / "state"
        / "signals"
        / "latest_expert_signals.json"
    ),
    "decisions": (
        PROJECT_ROOT
        / "state"
        / "decisions"
        / "latest_alliance_decisions.json"
    ),
    "shortlist": (
        PROJECT_ROOT
        / "state"
        / "decisions"
        / "latest_shortlist.json"
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "signals",
            "decisions",
            "shortlist",
            "summary",
        ],
    )

    arguments = parser.parse_args()

    if arguments.command in FILES:
        path = FILES[
            arguments.command
        ]

        if not path.exists():
            raise SystemExit(
                f"{path} has not been created"
            )

        print(
            json.dumps(
                json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                ),
                indent=2,
            )
        )

        return

    shortlist_path = FILES[
        "shortlist"
    ]

    decision_path = FILES[
        "decisions"
    ]

    if not shortlist_path.exists():
        raise SystemExit(
            "Segment 2 has not run"
        )

    shortlist = json.loads(
        shortlist_path.read_text(
            encoding="utf-8"
        )
    )

    decisions = json.loads(
        decision_path.read_text(
            encoding="utf-8"
        )
    )

    print()
    print(
        "OANDA MASTER AI — SEGMENT 2"
    )
    print("=" * 88)
    print(
        f"Candidate decisions: "
        f"{shortlist.get('candidate_count', 0)}"
    )
    print(
        f"Final shortlist:      "
        f"{shortlist.get('shortlist_count', 0)}"
    )
    print(
        f"Total decisions:      "
        f"{len(decisions.get('decisions', []))}"
    )
    print(
        "Automatic execution: false"
    )
    print()

    for item in shortlist.get(
        "shortlist",
        [],
    ):
        print(
            f"{item['instrument']:8} "
            f"{item['side'].upper():4} "
            f"score={item['alliance_score']:6.2f} "
            f"experts={item['confirming_experts']} "
            f"RR={item['reward_risk']:.2f}"
        )


if __name__ == "__main__":
    main()
