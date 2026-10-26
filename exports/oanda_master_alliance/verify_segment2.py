from __future__ import annotations

import json
from pathlib import Path

from brain.alliance_engine import (
    AllianceEngine,
)
from core.common import ROOT
from experts.registry import (
    build_experts,
)


def main() -> None:
    # Segment 2 requires a current Segment 1 market snapshot.
    # Refresh the foundation before evaluating expert signals.
    from run_foundation_snapshot import (
        main as run_foundation_snapshot,
    )

    print(
        "Refreshing Segment 1 foundation snapshot..."
    )

    run_foundation_snapshot()

    engine = AllianceEngine()

    experts = build_experts(
        engine.config
    )

    expected = {
        "velez",
        "raschke",
        "brooks",
        "connors",
        "bollinger",
        "crabel",
        "williams",
    }

    names = {
        expert.expert_id
        for expert in experts
    }

    missing = expected - names

    if missing:
        raise SystemExit(
            "Missing experts: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    result = engine.run()

    required = [
        (
            ROOT
            / "state"
            / "signals"
            / "latest_expert_signals.json"
        ),
        (
            ROOT
            / "state"
            / "decisions"
            / "latest_alliance_decisions.json"
        ),
        (
            ROOT
            / "state"
            / "decisions"
            / "latest_shortlist.json"
        ),
    ]

    for path in required:
        if not path.exists():
            raise SystemExit(
                f"Missing output: {path}"
            )

    shortlist = json.loads(
        required[-1].read_text(
            encoding="utf-8"
        )
    )

    if shortlist.get(
        "automatic_execution"
    ) is not False:
        raise SystemExit(
            "Automatic execution guard failed"
        )

    print()
    print(
        "OANDA MASTER AI — SEGMENT 2"
    )
    print("=" * 72)
    print(
        "Status:                 PASSED"
    )
    print(
        f"Expert engines:         {len(experts)}"
    )
    print(
        f"Pairs evaluated:        "
        f"{result['pairs_evaluated']}"
    )
    print(
        f"Expert signals:         "
        f"{result['expert_signals']}"
    )
    print(
        f"Alliance decisions:     "
        f"{result['alliance_decisions']}"
    )
    print(
        f"Candidates:             "
        f"{result['candidates']}"
    )
    print(
        f"Correlation shortlist:  "
        f"{result['shortlist']}"
    )
    print(
        "Automatic execution:    False"
    )
    print()
    print(
        "Segment 2 verification passed."
    )


if __name__ == "__main__":
    main()
