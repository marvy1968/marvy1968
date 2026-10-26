from __future__ import annotations

from risk.position_sizing import (
    calculate_position_size,
)
from trade_management.manager import (
    TradeManager,
)
import yaml
from core.common import ROOT


def main() -> None:
    config = yaml.safe_load(
        (
            ROOT
            / "config/segment3.yaml"
        ).read_text(
            encoding="utf-8"
        )
    )

    size = calculate_position_size(
        instrument="EUR_USD",
        side="buy",
        equity=10000.0,
        risk_pct=0.35,
        entry_price=1.10000,
        stop_price=1.09950,
        minimum_units=1,
        maximum_units=100000,
    )

    assert size.units > 0
    assert size.signed_units > 0
    assert round(
        size.stop_pips,
        2,
    ) == 5.0

    manager = TradeManager(
        config
    )

    preview = manager.preview_position(
        {
            "instrument": "EUR_USD",
            "side": "buy",
            "entry_price": 1.10000,
            "initial_stop_price": 1.09950,
            "current_stop_price": 1.09950,
            "first_partial_taken": False,
        },
        current_price=1.10050,
    )

    assert preview["achieved_r"] >= 0.999999
    assert (
        preview[
            "proposed_stop_price"
        ]
        >= 1.10000
    )
    assert (
        preview[
            "partial_close_required"
        ]
        is True
    )

    segment3 = config["segment3"]

    assert (
        segment3["practice_only"]
        is True
    )

    assert (
        segment3[
            "automatic_execution"
        ]
        is False
    )

    assert (
        segment3[
            "practice_order_submission_enabled"
        ]
        is False
    )

    print()
    print(
        "OANDA MASTER AI — SEGMENT 3"
    )
    print("=" * 72)
    print("Status:                    PASSED")
    print(
        f"Test units:                {size.units}"
    )
    print(
        f"Test signed units:         {size.signed_units}"
    )
    print(
        f"Test stop pips:            {size.stop_pips:.2f}"
    )
    print(
        "Break-even adjustment:    "
        f"{preview['stop_adjustment_required']}"
    )
    print(
        "Partial-profit trigger:   "
        f"{preview['partial_close_required']}"
    )
    print(
        "Practice only:            "
        f"{segment3['practice_only']}"
    )
    print(
        "Automatic execution:      "
        f"{segment3['automatic_execution']}"
    )
    print(
        "Submission enabled:       "
        f"{segment3['practice_order_submission_enabled']}"
    )
    print()
    print(
        "Segment 3 verification passed."
    )


if __name__ == "__main__":
    main()
