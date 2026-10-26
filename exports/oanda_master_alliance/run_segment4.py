from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


from segment4.daemon import (
    Segment4Daemon,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master AI Segment 4A-1 "
            "runtime daemon"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run one orchestration cycle and exit."
        ),
    )

    parser.add_argument(
        "--force-session",
        action="store_true",
        help=(
            "Permit a research cycle when the "
            "session manager would otherwise skip it. "
            "This does not enable order submission."
        ),
    )

    return parser


def main() -> None:
    arguments = (
        build_parser()
        .parse_args()
    )

    daemon = Segment4Daemon(
        once=arguments.once,
        force_session=(
            arguments.force_session
        ),
    )

    raise SystemExit(
        daemon.run()
    )


if __name__ == "__main__":
    main()
