from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.common import ROOT


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def append_jsonl(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                payload,
                default=str,
            )
            + "\n"
        )


class TradeManager:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config
        self.segment3 = config["segment3"]
        self.management = self.segment3[
            "management"
        ]
        self.files = self.segment3["files"]

    def path(
        self,
        key: str,
    ) -> Path:
        return ROOT / self.files[key]

    @staticmethod
    def current_r(
        *,
        side: str,
        entry_price: float,
        stop_price: float,
        current_price: float,
    ) -> float:
        initial_risk = abs(
            entry_price - stop_price
        )

        if initial_risk <= 0:
            return 0.0

        if side == "buy":
            movement = (
                current_price - entry_price
            )
        else:
            movement = (
                entry_price - current_price
            )

        # Normalize floating-point noise near management thresholds.
        # For example, a mathematically exact +1.0R move can otherwise
        # evaluate as 0.9999999999998 and fail the profit trigger.
        return round(
            movement / initial_risk,
            10,
        )

    def proposed_stop(
        self,
        *,
        side: str,
        entry_price: float,
        initial_stop: float,
        current_price: float,
        current_stop: float,
    ) -> tuple[float, list[str]]:
        achieved_r = self.current_r(
            side=side,
            entry_price=entry_price,
            stop_price=initial_stop,
            current_price=current_price,
        )

        initial_distance = abs(
            entry_price - initial_stop
        )

        proposed = current_stop
        reasons: list[str] = []

        if achieved_r >= float(
            self.management[
                "break_even_trigger_r"
            ]
        ):
            lock_r = float(
                self.management[
                    "break_even_lock_r"
                ]
            )

            if side == "buy":
                break_even_stop = (
                    entry_price
                    + initial_distance
                    * lock_r
                )
                proposed = max(
                    proposed,
                    break_even_stop,
                )
            else:
                break_even_stop = (
                    entry_price
                    - initial_distance
                    * lock_r
                )
                proposed = min(
                    proposed,
                    break_even_stop,
                )

            reasons.append(
                "break_even_ratchet"
            )

        if achieved_r >= float(
            self.management[
                "trailing_start_r"
            ]
        ):
            distance_r = float(
                self.management[
                    "trailing_distance_r"
                ]
            )

            if achieved_r >= float(
                self.management[
                    "second_trailing_start_r"
                ]
            ):
                distance_r = float(
                    self.management[
                        "second_trailing_distance_r"
                    ]
                )

            distance = (
                initial_distance
                * distance_r
            )

            if side == "buy":
                trailing_stop = (
                    current_price - distance
                )
                proposed = max(
                    proposed,
                    trailing_stop,
                )
            else:
                trailing_stop = (
                    current_price + distance
                )
                proposed = min(
                    proposed,
                    trailing_stop,
                )

            reasons.append(
                "virtual_trailing_stop"
            )

        return proposed, reasons

    def preview_position(
        self,
        position: dict[str, Any],
        current_price: float,
    ) -> dict[str, Any]:
        side = str(
            position["side"]
        )
        entry = float(
            position["entry_price"]
        )
        initial_stop = float(
            position["initial_stop_price"]
        )
        current_stop = float(
            position.get(
                "current_stop_price",
                initial_stop,
            )
        )

        achieved_r = self.current_r(
            side=side,
            entry_price=entry,
            stop_price=initial_stop,
            current_price=current_price,
        )

        proposed_stop, reasons = (
            self.proposed_stop(
                side=side,
                entry_price=entry,
                initial_stop=initial_stop,
                current_price=current_price,
                current_stop=current_stop,
            )
        )

        partial_close = (
            achieved_r
            >= float(
                self.management[
                    "first_profit_trigger_r"
                ]
            )
            and not bool(
                position.get(
                    "first_partial_taken",
                    False,
                )
            )
        )

        result = {
            "timestamp": utc_now_iso(),
            "instrument": (
                position["instrument"]
            ),
            "side": side,
            "current_price": current_price,
            "achieved_r": achieved_r,
            "current_stop_price": (
                current_stop
            ),
            "proposed_stop_price": (
                proposed_stop
            ),
            "stop_adjustment_required": (
                proposed_stop
                != current_stop
            ),
            "partial_close_required": (
                partial_close
            ),
            "partial_close_fraction": (
                float(
                    self.management[
                        "first_profit_close_fraction"
                    ]
                )
                if partial_close
                else 0.0
            ),
            "reasons": reasons,
            "automatic_action": False,
        }

        append_jsonl(
            self.path(
                "management_log"
            ),
            result,
        )

        return result
