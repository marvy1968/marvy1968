from risk.position_sizing import (
    calculate_position_size,
)


def test_long_position_size() -> None:
    result = calculate_position_size(
        instrument="EUR_USD",
        side="buy",
        equity=10000,
        risk_pct=0.50,
        entry_price=1.1000,
        stop_price=1.0990,
        minimum_units=1,
        maximum_units=100000,
    )

    assert result.units > 0
    assert result.signed_units > 0


def test_short_position_size() -> None:
    result = calculate_position_size(
        instrument="USD_CHF",
        side="sell",
        equity=10000,
        risk_pct=0.50,
        entry_price=0.8050,
        stop_price=0.8060,
        minimum_units=1,
        maximum_units=100000,
    )

    assert result.units > 0
    assert result.signed_units < 0
