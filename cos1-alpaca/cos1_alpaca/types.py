"""Shared records. Prices are dollars. Times are timezone-aware."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    ts: datetime | None = None


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime
    close: datetime
    half_day: bool


@dataclass
class Regime:
    name: str  # up, down, chop
    sma_fast: float | None = None
    sma_slow: float | None = None
    slope: float | None = None
    atr_pct: float | None = None
    reason: str = ""


@dataclass
class Signal:
    symbol: str
    side: str  # long or short
    mode: str  # day, swing, scalp
    expert: str
    signal_id: str
    entry: float
    stop: float
    target: float | None
    target_r: float
    log_only: bool
    reason: str
    bar_ts: datetime


@dataclass
class BracketPlan:
    symbol: str
    qty: int
    side: str  # buy or sell
    stop: float
    take_profit: float
    client_order_id: str
    time_in_force: str
    mode: str
    expert: str
    signal_id: str
    entry_hint: float
    log_only: bool = False


@dataclass
class Asset:
    symbol: str
    tradable: bool
    shortable: bool
    easy_to_borrow: bool


@dataclass
class BrokerPosition:
    symbol: str
    qty: float
    side: str  # long or short
    avg_price: float
    current_price: float


@dataclass
class BrokerOrder:
    id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    type: str
    limit_price: float | None = None
    stop_price: float | None = None
    filled_avg_price: float | None = None
    legs: list = field(default_factory=list)


@dataclass
class Fill:
    id: str
    ts: datetime
    symbol: str
    side: str  # buy or sell
    qty: float
    price: float
    fee: float = 0.0
    order_id: str = ""
    client_order_id: str = ""
    mode: str = ""
    expected_price: float | None = None


@dataclass
class Account:
    equity: float
    cash: float


@dataclass
class MarketBundle:
    daily: dict[str, list[Bar]]
    min5: dict[str, list[Bar]]
    min1: dict[str, list[Bar]]
    quotes: dict[str, Quote]
