from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


PATHS = {
    "status": (
        ROOT
        / "state"
        / "decisions"
        / "segment2_status.json"
    ),
    "decisions": (
        ROOT
        / "state"
        / "decisions"
        / "latest_decisions.json"
    ),
    "shortlist": (
        ROOT
        / "state"
        / "decisions"
        / "latest_shortlist.json"
    ),
    "market": (
        ROOT
        / "state"
        / "market_snapshot.json"
    ),
    "regimes": (
        ROOT
        / "state"
        / "regime_snapshot.json"
    ),
}


def main() -> None:
    command = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "status"
    )

    if command not in PATHS:
        raise SystemExit(
            "Choose: "
            + ", ".join(
                PATHS
            )
        )

    path = PATHS[
        command
    ]

    if not path.exists():
        raise SystemExit(
            f"{path} has not been created"
        )

    payload = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
