from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any


JPY_QUOTE = {
    "EUR_JPY",
    "GBP_JPY",
    "USD_JPY",
    "AUD_JPY",
    "NZD_JPY",
    "CAD_JPY",
    "CHF_JPY",
}


@dataclass(frozen=True)
class PositionSize:
    instrument: str
    side: str
    equity: float
    risk_pct: float
    risk_amount: float
    entry_price: float
    stop_price: float
    stop_distance: float
    stop_pips: float
    pip_size: float
    estimated_pip_value_per_unit: float
    units: int
    signed_units: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "side": self.side,
            "equity": self.equity,
            "risk_pct": self.risk_pct,
            "risk_amount": self.risk_amount,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "stop_distance": self.stop_distance,
            "stop_pips": self.stop_pips,
            "pip_size": self.pip_size,
            "estimated_pip_value_per_unit": (
                self.estimated_pip_value_per_unit
            ),
            "units": self.units,
            "signed_units": self.signed_units,
        }


def pip_size(instrument: str) -> float:
    return 0.01 if instrument.upper() in JPY_QUOTE else 0.0001


def currencies(instrument: str) -> tuple[str, str]:
    pieces = instrument.upper().split("_")

    if len(pieces) != 2:
        raise ValueError(
            f"Unsupported instrument format: {instrument}"
        )

    return pieces[0], pieces[1]


def estimate_pip_value_per_unit(
    instrument: str,
    entry_price: float,
    account_currency: str = "USD",
) -> float:
    base, quote = currencies(instrument)
    size = pip_size(instrument)
    account_currency = account_currency.upper()

    if quote == account_currency:
        return size

    if base == account_currency:
        if entry_price <= 0:
            raise ValueError("Entry price must be positive.")

        return size / entry_price

    # Conservative approximation for non-USD-cross conversion.
    # Segment 3 currently trades the configured USD major pairs.
    return size


def calculate_position_size(
    *,
    instrument: str,
    side: str,
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    minimum_units: int,
    maximum_units: int,
    account_currency: str = "USD",
) -> PositionSize:
    side = side.lower().strip()

    if side not in {"buy", "sell"}:
        raise ValueError(f"Unsupported side: {side}")

    if equity <= 0:
        raise ValueError("Account equity must be positive.")

    if risk_pct <= 0:
        raise ValueError("Risk percentage must be positive.")

    stop_distance = abs(entry_price - stop_price)

    if stop_distance <= 0:
        raise ValueError("Stop distance must be positive.")

    size = pip_size(instrument)
    stop_pips = stop_distance / size

    pip_value = estimate_pip_value_per_unit(
        instrument,
        entry_price,
        account_currency,
    )

    risk_amount = equity * (risk_pct / 100.0)

    raw_units = risk_amount / (
        stop_pips * pip_value
    )

    units = int(
        Decimal(str(raw_units)).quantize(
            Decimal("1"),
            rounding=ROUND_DOWN,
        )
    )

    units = max(
        int(minimum_units),
        min(
            int(maximum_units),
            units,
        ),
    )

    signed_units = units if side == "buy" else -units

    return PositionSize(
        instrument=instrument,
        side=side,
        equity=float(equity),
        risk_pct=float(risk_pct),
        risk_amount=float(risk_amount),
        entry_price=float(entry_price),
        stop_price=float(stop_price),
        stop_distance=float(stop_distance),
        stop_pips=float(stop_pips),
        pip_size=float(size),
        estimated_pip_value_per_unit=float(
            pip_value
        ),
        units=units,
        signed_units=signed_units,
    )
