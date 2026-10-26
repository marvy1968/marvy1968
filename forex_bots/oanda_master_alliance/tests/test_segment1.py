from verify_segment1 import (
    synthetic_frame,
)

from core.common import load_config
from core.indicators import (
    build_indicator_frame,
)
from market.regime_engine import (
    RegimeEngine,
)


def test_indicators_build() -> None:
    config = load_config()
    frame = synthetic_frame()

    result = build_indicator_frame(
        frame,
        config["regime"],
    )

    assert not result.empty
    assert "adx" in result.columns
    assert "atr" in result.columns
    assert "rsi" in result.columns


def test_regime_classification() -> None:
    config = load_config()
    engine = RegimeEngine(config)

    result = engine.classify_timeframe(
        "M5",
        synthetic_frame(),
    )

    assert result.regime in {
        "strong_trending",
        "trending",
        "ranging",
        "compression",
        "breakout_transition",
        "mixed",
    }
