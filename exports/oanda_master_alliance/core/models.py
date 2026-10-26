from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PriceQuote:
    instrument: str
    timestamp: str
    bid: float
    ask: float
    midpoint: float
    spread: float
    spread_pips: float
    tradeable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IndicatorSnapshot:
    timeframe: str
    timestamp: str
    close: float
    ema_fast: float
    ema_slow: float
    ema_context: float
    atr: float
    atr_percentile: float
    adx: float
    plus_di: float
    minus_di: float
    rsi: float
    bollinger_upper: float
    bollinger_middle: float
    bollinger_lower: float
    bollinger_width: float
    bollinger_width_percentile: float
    price_slope: float
    volume_ratio: float
    trend_votes: int
    range_votes: int
    direction: str
    regime: str
    reasons: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PairRegime:
    instrument: str
    timestamp: str
    regime: str
    direction: str
    confidence: float
    confirmations: int
    timeframe_results: list[
        IndicatorSnapshot
    ]
    data_quality_passed: bool
    rejection_reasons: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)

        payload["timeframe_results"] = [
            result.to_dict()
            for result in self.timeframe_results
        ]

        return payload


@dataclass
class CurrencyStrength:
    timestamp: str
    values: dict[str, float]
    rankings: list[str]
    pair_momentum: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketSnapshot:
    timestamp: str
    account_environment: str
    session: str
    active_sessions: list[str]
    quotes: dict[str, PriceQuote]
    regimes: dict[str, PairRegime]
    currency_strength: CurrencyStrength | None
    data_quality_passed: bool
    errors: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "account_environment": (
                self.account_environment
            ),
            "session": self.session,
            "active_sessions": (
                self.active_sessions
            ),
            "quotes": {
                key: value.to_dict()
                for key, value
                in self.quotes.items()
            },
            "regimes": {
                key: value.to_dict()
                for key, value
                in self.regimes.items()
            },
            "currency_strength": (
                self.currency_strength.to_dict()
                if self.currency_strength
                else None
            ),
            "data_quality_passed": (
                self.data_quality_passed
            ),
            "errors": self.errors,
        }
