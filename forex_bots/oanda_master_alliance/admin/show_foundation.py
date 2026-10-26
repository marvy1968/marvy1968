from __future__ import annotations

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



import argparse
import json
from pathlib import Path

from core.common import ROOT


FILES = {
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
    "strength": (
        ROOT
        / "state"
        / "currency_strength.json"
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Show OANDA Master AI "
            "Segment 1 state"
        )
    )

    parser.add_argument(
        "command",
        choices=sorted(FILES),
    )

    arguments = parser.parse_args()

    path: Path = FILES[
        arguments.command
    ]

    if not path.exists():
        raise SystemExit(
            f"{path} has not been created"
        )

    payload = json.loads(
        path.read_text(
            encoding="utf-8",
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
