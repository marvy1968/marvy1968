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


from segment4b3.common import (
    load_config,
    read_json,
    storage_path,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "summary",
            "promotion",
            "monte-carlo",
            "walk-forward",
            "stability",
            "experts",
            "pairs",
            "setups",
            "sessions",
            "regimes",
            "hall-of-fame",
            "retirement-watchlist",
        ],
    )

    arguments = parser.parse_args()

    config = load_config()

    mapping = {
        "promotion": (
            "promotion_decision"
        ),
        "monte-carlo": (
            "monte_carlo"
        ),
        "walk-forward": (
            "walk_forward"
        ),
        "stability": (
            "stability"
        ),
        "experts": (
            "expert_scorecard"
        ),
        "pairs": (
            "pair_scorecard"
        ),
        "setups": (
            "setup_scorecard"
        ),
        "sessions": (
            "session_scorecard"
        ),
        "regimes": (
            "regime_scorecard"
        ),
        "hall-of-fame": (
            "hall_of_fame"
        ),
        "retirement-watchlist": (
            "retirement_watchlist"
        ),
    }

    if arguments.command == "summary":
        payload = read_json(
            storage_path(
                config,
                "latest_analysis",
            ),
            default={},
        )

        metrics = payload.get(
            "metrics",
            {},
        )

        if not isinstance(
            metrics,
            dict,
        ):
            metrics = {}

        print()
        print(
            "OANDA MASTER AI — SEGMENT 4B-3"
        )
        print("=" * 80)
        print(
            f"Closed shadow trades:       {payload.get('closed_shadow_trades', 0)}"
        )
        print(
            f"Open shadow trades:         {payload.get('open_shadow_trades', 0)}"
        )
        print(
            f"Win rate:                   {100 * float(metrics.get('win_rate', 0.0)):.2f}%"
        )
        print(
            f"Profit factor:              {float(metrics.get('profit_factor', 0.0)):.2f}"
        )
        print(
            f"Expectancy:                 {float(metrics.get('expectancy_r', 0.0)):.3f}R"
        )
        print(
            f"Net result:                 {float(metrics.get('net_r', 0.0)):.2f}R"
        )
        print(
            f"Maximum drawdown:           {float(metrics.get('maximum_drawdown_r', 0.0)):.2f}R"
        )
        print(
            f"Monte Carlo passed:         {payload.get('monte_carlo_passed', False)}"
        )
        print(
            f"Walk-forward eligible:      {payload.get('walk_forward_eligible', False)}"
        )
        print(
            f"Walk-forward passed:        {payload.get('walk_forward_passed', False)}"
        )
        print(
            f"Stability:                  {payload.get('stability_status', 'unknown')}"
        )
        print(
            f"Checkpoint:                 {payload.get('promotion_checkpoint', 'unknown')}"
        )
        print(
            f"Readiness:                  {payload.get('readiness', 'unknown')}"
        )
        print(
            f"Eligible for manual review: {payload.get('eligible_for_manual_review', False)}"
        )
        print(
            "Automatic promotion:        False"
        )
        print(
            "Automatic execution:        False"
        )
        print(
            "Orders submitted:           0"
        )

        return

    payload = read_json(
        storage_path(
            config,
            mapping[
                arguments.command
            ],
        ),
        default={},
    )

    print(
        json.dumps(
            payload,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
