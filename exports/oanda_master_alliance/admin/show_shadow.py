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

if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


from segment4b2.common import (
    load_config,
    read_json,
    storage_path,
)


def print_json(
    payload,
) -> None:
    print(
        json.dumps(
            payload,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "summary",
            "queue",
            "open",
            "closed",
            "performance",
            "experts",
            "pairs",
            "setups",
            "promotion",
        ],
    )

    args = parser.parse_args()

    config = load_config()

    if args.command == "summary":
        cycle = read_json(
            storage_path(
                config,
                "latest_cycle",
            ),
            default={},
        )

        performance = read_json(
            storage_path(
                config,
                "performance_summary",
            ),
            default={},
        )

        overall = performance.get(
            "overall",
            {},
        )

        if not isinstance(
            overall,
            dict,
        ):
            overall = {}

        print()
        print(
            "OANDA MASTER AI — SEGMENT 4B-2"
        )
        print("=" * 80)
        print(
            f"Latest cycle successful: {cycle.get('successful', False)}"
        )
        print(
            f"Candidates loaded:       {cycle.get('candidates_loaded', 0)}"
        )
        print(
            f"Candidates approved:     {cycle.get('candidates_approved', 0)}"
        )
        print(
            f"Trades opened:           {cycle.get('trades_opened', 0)}"
        )
        print(
            f"Open trades:             {cycle.get('open_trade_count', 0)}"
        )
        print(
            f"Closed trades:           {cycle.get('closed_trade_count', 0)}"
        )
        print(
            f"Wins:                    {overall.get('wins', 0)}"
        )
        print(
            f"Losses:                  {overall.get('losses', 0)}"
        )
        print(
            f"Net R:                   {overall.get('net_r', 0.0)}"
        )
        print(
            f"Automatic execution:     False"
        )
        print(
            f"Orders submitted:        0"
        )

        return

    mapping = {
        "queue": "queue",
        "open": "open_trades",
        "closed": "closed_trades",
        "performance": (
            "performance_summary"
        ),
        "experts": (
            "expert_performance"
        ),
        "pairs": "pair_performance",
        "setups": (
            "setup_performance"
        ),
        "promotion": (
            "promotion_report"
        ),
    }

    print_json(
        read_json(
            storage_path(
                config,
                mapping[
                    args.command
                ],
            ),
            default={},
        )
    )


if __name__ == "__main__":
    main()
