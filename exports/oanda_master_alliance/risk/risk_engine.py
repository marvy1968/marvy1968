from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.common import ROOT
from risk.position_sizing import (
    PositionSize,
    calculate_position_size,
    currencies,
    pip_size,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_json(
    path: Path,
    default: Any,
) -> Any:
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return default


@dataclass
class RiskDecision:
    approved: bool
    instrument: str
    side: str
    reasons: list[str] = field(
        default_factory=list
    )
    warnings: list[str] = field(
        default_factory=list
    )
    position_size: PositionSize | None = None
    risk_pct: float = 0.0
    open_risk_r: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "instrument": self.instrument,
            "side": self.side,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "risk_pct": self.risk_pct,
            "open_risk_r": self.open_risk_r,
            "position_size": (
                self.position_size.to_dict()
                if self.position_size
                else None
            ),
        }


class RiskEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config
        self.segment3 = config["segment3"]
        self.risk = self.segment3["risk"]
        self.execution = self.segment3[
            "execution"
        ]
        self.files = self.segment3["files"]

    def file_path(
        self,
        key: str,
    ) -> Path:
        return ROOT / self.files[key]

    def load_open_positions(
        self,
    ) -> list[dict[str, Any]]:
        payload = load_json(
            self.file_path("open_positions"),
            {
                "positions": [],
            },
        )

        return list(
            payload.get(
                "positions",
                [],
            )
        )

    def pause_status(
        self,
    ) -> tuple[bool, str]:
        pause = load_json(
            self.file_path("pause_file"),
            {},
        )

        until_value = pause.get(
            "pause_until"
        )

        if not until_value:
            return False, ""

        try:
            until = datetime.fromisoformat(
                until_value
            )
        except ValueError:
            return False, ""

        if until.tzinfo is None:
            until = until.replace(
                tzinfo=timezone.utc
            )

        if utc_now() < until:
            return (
                True,
                str(
                    pause.get(
                        "reason",
                        "risk_pause",
                    )
                ),
            )

        return False, ""

    def set_loss_pause(
        self,
        reason: str,
    ) -> None:
        minutes = int(
            self.risk["pause_minutes"]
        )

        until = (
            utc_now()
            + timedelta(
                minutes=minutes
            )
        )

        path = self.file_path(
            "pause_file"
        )
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            json.dumps(
                {
                    "paused": True,
                    "reason": reason,
                    "pause_until": (
                        until.isoformat()
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def instrument_exposure(
        instrument: str,
        side: str,
    ) -> dict[str, int]:
        base, quote = currencies(
            instrument
        )

        if side == "buy":
            return {
                base: 1,
                quote: -1,
            }

        return {
            base: -1,
            quote: 1,
        }

    def current_currency_exposure(
        self,
        positions: list[dict[str, Any]],
    ) -> dict[str, int]:
        result: dict[str, int] = {}

        for position in positions:
            instrument = str(
                position.get(
                    "instrument",
                    "",
                )
            )
            side = str(
                position.get(
                    "side",
                    "",
                )
            ).lower()

            if not instrument or side not in {
                "buy",
                "sell",
            }:
                continue

            for currency, direction in (
                self.instrument_exposure(
                    instrument,
                    side,
                ).items()
            ):
                result[currency] = (
                    result.get(
                        currency,
                        0,
                    )
                    + direction
                )

        return result

    def select_risk_pct(
        self,
        candidate: dict[str, Any],
    ) -> float:
        score = float(
            candidate.get(
                "alliance_score",
                0.0,
            )
        )

        confirmations = int(
            candidate.get(
                "confirming_experts",
                0,
            )
        )

        if (
            score
            >= float(
                self.risk[
                    "strong_setup_score"
                ]
            )
            and confirmations >= 3
        ):
            selected = float(
                self.risk[
                    "strong_setup_risk_pct"
                ]
            )
        else:
            selected = float(
                self.risk[
                    "standard_risk_pct"
                ]
            )

        return min(
            selected,
            float(
                self.risk[
                    "maximum_risk_pct"
                ]
            ),
        )

    def evaluate(
        self,
        *,
        candidate: dict[str, Any],
        account: dict[str, Any],
        runtime: dict[str, Any],
    ) -> RiskDecision:
        instrument = str(
            candidate.get(
                "instrument",
                "",
            )
        ).upper()

        side = str(
            candidate.get(
                "side",
                "",
            )
        ).lower()

        reasons: list[str] = []
        warnings: list[str] = []

        if side not in {"buy", "sell"}:
            reasons.append(
                "invalid_side"
            )

        if (
            candidate.get("decision")
            != "candidate"
        ):
            reasons.append(
                "segment2_not_candidate"
            )

        score = float(
            candidate.get(
                "alliance_score",
                0.0,
            )
        )

        if score < float(
            self.segment3[
                "minimum_alliance_score"
            ]
        ):
            reasons.append(
                "alliance_score_below_minimum"
            )

        confirming = int(
            candidate.get(
                "confirming_experts",
                0,
            )
        )

        if confirming < int(
            self.segment3[
                "minimum_confirming_experts"
            ]
        ):
            reasons.append(
                "insufficient_confirming_experts"
            )

        reward_risk = float(
            candidate.get(
                "reward_risk",
                0.0,
            )
        )

        if reward_risk < float(
            self.segment3[
                "minimum_reward_risk"
            ]
        ):
            reasons.append(
                "reward_risk_below_minimum"
            )

        if (
            self.file_path(
                "kill_switch"
            ).exists()
        ):
            reasons.append(
                "kill_switch_enabled"
            )

        paused, pause_reason = (
            self.pause_status()
        )

        if paused:
            reasons.append(
                f"risk_paused:{pause_reason}"
            )

        realized_daily_r = float(
            runtime.get(
                "realized_daily_r",
                0.0,
            )
        )

        if realized_daily_r <= -abs(
            float(
                self.risk[
                    "daily_loss_limit_r"
                ]
            )
        ):
            reasons.append(
                "daily_loss_limit_reached"
            )

        realized_weekly_r = float(
            runtime.get(
                "realized_weekly_r",
                0.0,
            )
        )

        if realized_weekly_r <= -abs(
            float(
                self.risk[
                    "weekly_loss_limit_r"
                ]
            )
        ):
            reasons.append(
                "weekly_loss_limit_reached"
            )

        consecutive_losses = int(
            runtime.get(
                "consecutive_losses",
                0,
            )
        )

        if consecutive_losses >= int(
            self.risk[
                "pause_after_losses"
            ]
        ):
            self.set_loss_pause(
                "consecutive_loss_limit"
            )
            reasons.append(
                "consecutive_loss_limit"
            )

        positions = self.load_open_positions()

        if len(positions) >= int(
            self.risk[
                "maximum_positions"
            ]
        ):
            reasons.append(
                "maximum_positions_reached"
            )

        if bool(
            self.execution.get(
                "reject_duplicate_instrument",
                True,
            )
        ):
            if any(
                str(
                    position.get(
                        "instrument",
                        "",
                    )
                ).upper()
                == instrument
                for position in positions
            ):
                reasons.append(
                    "instrument_already_open"
                )

        base, quote = currencies(
            instrument
        )

        same_currency_count = sum(
            1
            for position in positions
            if base in str(
                position.get(
                    "instrument",
                    "",
                )
            )
            or quote in str(
                position.get(
                    "instrument",
                    "",
                )
            )
        )

        if same_currency_count >= int(
            self.risk[
                "maximum_same_currency_positions"
            ]
        ):
            reasons.append(
                "same_currency_position_limit"
            )

        exposure = (
            self.current_currency_exposure(
                positions
            )
        )

        proposed = (
            self.instrument_exposure(
                instrument,
                side,
            )
        )

        maximum_directional = int(
            self.risk[
                "maximum_directional_currency_exposure"
            ]
        )

        for currency, direction in (
            proposed.items()
        ):
            future = (
                exposure.get(
                    currency,
                    0,
                )
                + direction
            )

            if abs(future) > maximum_directional:
                reasons.append(
                    "currency_direction_limit:"
                    f"{currency}"
                )

        current_open_risk = sum(
            float(
                position.get(
                    "initial_risk_r",
                    1.0,
                )
            )
            for position in positions
        )

        if current_open_risk + 1.0 > float(
            self.risk[
                "maximum_open_risk_r"
            ]
        ):
            reasons.append(
                "maximum_open_risk_reached"
            )

        entry = float(
            candidate.get(
                "entry_price",
                0.0,
            )
        )
        stop = float(
            candidate.get(
                "stop_price",
                0.0,
            )
        )

        stop_pips = (
            abs(entry - stop)
            / pip_size(instrument)
            if entry > 0
            else 0.0
        )

        if stop_pips < float(
            self.execution[
                "minimum_stop_pips"
            ]
        ):
            reasons.append(
                "stop_too_tight"
            )

        if stop_pips > float(
            self.execution[
                "maximum_stop_pips"
            ]
        ):
            reasons.append(
                "stop_too_wide"
            )

        equity = float(
            account.get(
                "NAV",
                account.get(
                    "nav",
                    account.get(
                        "balance",
                        0.0,
                    ),
                ),
            )
        )

        risk_pct = self.select_risk_pct(
            candidate
        )

        position_size = None

        if not reasons:
            try:
                position_size = (
                    calculate_position_size(
                        instrument=instrument,
                        side=side,
                        equity=equity,
                        risk_pct=risk_pct,
                        entry_price=entry,
                        stop_price=stop,
                        minimum_units=int(
                            self.risk[
                                "minimum_units"
                            ]
                        ),
                        maximum_units=int(
                            self.risk[
                                "maximum_units"
                            ]
                        ),
                    )
                )
            except Exception as exc:
                reasons.append(
                    "position_size_error:"
                    f"{type(exc).__name__}:"
                    f"{exc}"
                )

        return RiskDecision(
            approved=not reasons,
            instrument=instrument,
            side=side,
            reasons=reasons,
            warnings=warnings,
            position_size=position_size,
            risk_pct=risk_pct,
            open_risk_r=(
                current_open_risk
            ),
        )
