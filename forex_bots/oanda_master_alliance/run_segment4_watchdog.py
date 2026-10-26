from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.watchdog import (
    Segment4Watchdog,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI Segment 4A-2 "
            "watchdog"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    arguments = parser.parse_args()

    watchdog = Segment4Watchdog(
        once=arguments.once
    )

    raise SystemExit(
        watchdog.run()
    )


if __name__ == "__main__":
    main()
