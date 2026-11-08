"""Turn a provider payload into bars, or refuse it.

HTTP 200 responses that carry Note, Information, or Error Message are not bars.
Daily bars are raw as-traded OHLC. Weekly adjusted bars keep that raw OHLC and
put the split-and-dividend adjusted close in its own field. Nothing here rescales
OHLC by the adjusted/raw ratio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .errors import LocalRefusal, ProviderMessage, StaleSeries, ValidationError
from .spec import (
    ADJUSTMENT_DAILY_RAW,
    COMPACT,
    DAILY,
    DEFAULT_MAX_CALENDAR_AGE_DAYS,
    SERIES_KEY,
    SYMBOL_RE,
    WEEKLY_ADJUSTED,
    adjusted_close_basis,
    adjustment_basis,
)

NY = ZoneInfo("America/New_York")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PRICE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_INT = re.compile(r"^[0-9]+$")
_MESSAGE_KEYS = ("Note", "Information", "Error Message")
_MESSAGE_CATEGORY = {"Note": "note", "Information": "information", "Error Message": "error_message"}

_DAILY_FIELDS = {
    "open": "1. open",
    "high": "2. high",
    "low": "3. low",
    "close": "4. close",
    "volume": "5. volume",
}
_WEEKLY_FIELDS = {
    "open": "1. open",
    "high": "2. high",
    "low": "3. low",
    "close": "4. close",
    "adjusted_close": "5. adjusted close",
    "volume": "6. volume",
    "dividend_amount": "7. dividend amount",
}
_ADJUSTED_LEFTOVERS = ("5. adjusted close", "6. volume", "7. dividend amount", "adjusted close")


@dataclass(frozen=True)
class SeriesRequest:
    symbol: str
    function: str
    outputsize: str | None


@dataclass(frozen=True)
class Bar:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    adjusted_close: Decimal | None = None
    dividend_amount: Decimal | None = None

    def provider_fields(self, function: str) -> dict[str, str]:
        def num(value: Decimal, integer: bool) -> str:
            text = format(value, "f")
            if integer and "." in text:
                text = text.split(".", 1)[0]
            return text

        if function == DAILY:
            return {
                "1. open": num(self.open, False),
                "2. high": num(self.high, False),
                "3. low": num(self.low, False),
                "4. close": num(self.close, False),
                "5. volume": num(self.volume, True),
            }
        if self.adjusted_close is None or self.dividend_amount is None:
            raise ValidationError("missing_series", "weekly bar is missing adjusted close or dividend")
        return {
            "1. open": num(self.open, False),
            "2. high": num(self.high, False),
            "3. low": num(self.low, False),
            "4. close": num(self.close, False),
            "5. adjusted close": num(self.adjusted_close, False),
            "6. volume": num(self.volume, True),
            "7. dividend amount": num(self.dividend_amount, False),
        }


@dataclass(frozen=True)
class ValidatedSeries:
    symbol: str
    function: str
    outputsize: str | None
    adjustment_basis: str
    ohlc_basis: str
    adjusted_close_basis: str
    timezone: str
    information: str
    last_refreshed: date
    bars: tuple[Bar, ...]

    @property
    def newest(self) -> date:
        return self.bars[-1].session_date

    @property
    def oldest(self) -> date:
        return self.bars[0].session_date


def parse_request(symbol: object, function: object, outputsize: object = None) -> SeriesRequest:
    if not isinstance(symbol, str) or not isinstance(function, str):
        raise LocalRefusal("symbol and function must be strings")
    if any(ch in symbol for ch in (",", " ", "\n", "\t", "/", "\\")):
        raise LocalRefusal("one symbol per call")
    normalized = symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(normalized):
        raise LocalRefusal("symbol is not a single ticker")
    if function not in SERIES_KEY:
        raise LocalRefusal("function is not an observed free endpoint")
    expected_size = COMPACT if function == DAILY else None
    if outputsize != expected_size:
        raise LocalRefusal("output size is not the observed free contract")
    return SeriesRequest(normalized, function, expected_size)


def as_of_ny(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValidationError("timezone", "as_of must be a timezone-aware datetime")
    return value.astimezone(NY)


def classify_provider_message(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in _MESSAGE_KEYS:
        if key in payload:
            return _MESSAGE_CATEGORY[key]
    return None


def validate_payload(
    payload: object,
    request: SeriesRequest,
    as_of: datetime,
    max_calendar_age_days: int = DEFAULT_MAX_CALENDAR_AGE_DAYS,
) -> ValidatedSeries:
    """Validate a decoded JSON object. A provider message is not parsed as bars."""
    if not isinstance(payload, dict):
        raise ValidationError("non_object", "payload is not a JSON object")
    category = classify_provider_message(payload)
    if category is not None:
        raise ProviderMessage(category)
    meta = payload.get("Meta Data")
    series = payload.get(SERIES_KEY[request.function])
    if not isinstance(meta, dict) or not isinstance(series, dict):
        raise ValidationError("missing_series", "metadata or series is missing")
    return validate_bars(meta, series, request, as_of, max_calendar_age_days)


def validate_bars(
    meta: dict,
    series: dict,
    request: SeriesRequest,
    as_of: datetime,
    max_calendar_age_days: int = DEFAULT_MAX_CALENDAR_AGE_DAYS,
) -> ValidatedSeries:
    _check_age_bound(max_calendar_age_days)
    observed = as_of_ny(as_of).date()
    symbol = _required_text(meta, "2. Symbol")
    if symbol != request.symbol:
        raise ValidationError("wrong_symbol", "metadata symbol does not match the request")
    information = _required_text(meta, "1. Information")
    if len(information) > 300:
        raise ValidationError("meta", "information field is too long")
    timezone = _required_text(meta, "5. Time Zone" if request.function == DAILY else "4. Time Zone")
    if timezone != "US/Eastern":
        raise ValidationError("bad_timezone", "series time zone is not US/Eastern")
    last_refreshed = _parse_date(_required_text(meta, "3. Last Refreshed"), "last_refreshed")
    if request.function == DAILY:
        output_size = _required_text(meta, "4. Output Size")
        if output_size != "Compact":
            raise ValidationError("output_size", "daily series is not compact")
    elif "4. Output Size" in meta:
        raise ValidationError("output_size", "weekly series is not expected to carry an output size")

    if not series:
        raise ValidationError("empty", "series has no bars")
    limit = 100 if request.function == DAILY else 3000
    if len(series) > limit:
        raise ValidationError("too_many_bars", "series is longer than the free-review cap")
    bars: list[Bar] = []
    seen: set[date] = set()
    for raw_date, raw_bar in series.items():
        session = _parse_date(raw_date, "session_date")
        if session in seen:
            raise ValidationError("duplicate_date", session.isoformat())
        seen.add(session)
        if session.weekday() >= 5:
            raise ValidationError("weekend_date", session.isoformat())
        if not isinstance(raw_bar, dict):
            raise ValidationError("illformed_ohlc", "bar is not an object")
        bars.append(_parse_bar(session, raw_bar, request.function))
    bars.sort(key=lambda bar: bar.session_date)
    if last_refreshed != bars[-1].session_date:
        raise ValidationError("last_refreshed_mismatch", "last refreshed does not match the newest session")
    newest = bars[-1].session_date
    if newest > observed:
        raise ValidationError("future_bar", newest.isoformat())
    age = (observed - newest).days
    if age > max_calendar_age_days:
        raise StaleSeries(f"newest session {newest.isoformat()} is {age} calendar days before {observed.isoformat()}")
    return ValidatedSeries(
        symbol=request.symbol,
        function=request.function,
        outputsize=request.outputsize,
        adjustment_basis=adjustment_basis(request.function),
        ohlc_basis="raw_unadjusted",
        adjusted_close_basis=adjusted_close_basis(request.function),
        timezone="US/Eastern",
        information=information,
        last_refreshed=last_refreshed,
        bars=tuple(bars),
    )


def _parse_bar(session: date, raw: dict, function: str) -> Bar:
    if function == DAILY:
        for key in raw:
            if "adjust" in str(key).lower() or "dividend" in str(key).lower() or key in _ADJUSTED_LEFTOVERS:
                raise ValidationError("unexpected_adjusted_field", "daily bar contains an adjusted field")
        fields = _DAILY_FIELDS
    else:
        fields = _WEEKLY_FIELDS
    parsed = {name: raw.get(key) for name, key in fields.items()}
    if any(value is None for value in parsed.values()):
        raise ValidationError("illformed_ohlc", "bar is missing a required field")
    open_ = _decimal(parsed["open"], allow_zero=False, integer=False)
    high = _decimal(parsed["high"], allow_zero=False, integer=False)
    low = _decimal(parsed["low"], allow_zero=False, integer=False)
    close = _decimal(parsed["close"], allow_zero=False, integer=False)
    volume = _decimal(parsed["volume"], allow_zero=False, integer=True)
    if high < low or high < max(open_, close) or low > min(open_, close):
        raise ValidationError("illformed_ohlc", session.isoformat())
    adjusted = None
    dividend = None
    if function == WEEKLY_ADJUSTED:
        adjusted = _decimal(parsed["adjusted_close"], allow_zero=False, integer=False)
        dividend = _decimal(parsed["dividend_amount"], allow_zero=True, integer=False)
    return Bar(session, open_, high, low, close, volume, adjusted, dividend)


def _decimal(value: object, *, allow_zero: bool, integer: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValidationError("illformed_ohlc", "numeric fields must be strings")
    text = value.strip()
    if not (_INT if integer else _PRICE).fullmatch(text):
        raise ValidationError("illformed_ohlc", "numeric field is not a plain decimal")
    number = Decimal(text)
    if number < 0 or (not allow_zero and number == 0):
        raise ValidationError("illformed_ohlc", "numeric field is outside the allowed range")
    return number


def _parse_date(value: object, what: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValidationError("bad_date", what)
    year, month, day = (int(part) for part in value.split("-"))
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValidationError("bad_date", what) from None


def _required_text(meta: dict, key: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("meta", f"missing {key}")
    return value.strip()


def _check_age_bound(max_calendar_age_days: int) -> None:
    from .spec import MAX_CALENDAR_AGE_DAYS

    if isinstance(max_calendar_age_days, bool) or not isinstance(max_calendar_age_days, int):
        raise ValidationError("stale", "calendar age bound is not an integer")
    if not 0 <= max_calendar_age_days <= MAX_CALENDAR_AGE_DAYS:
        raise ValidationError("stale", "calendar age bound is outside 0..30")


def assert_basis(series: ValidatedSeries) -> None:
    """Refuse a series whose labels do not match the function that produced it."""
    if series.ohlc_basis != "raw_unadjusted":
        raise ValidationError("mixed_adjustment", "OHLC is not labeled raw")
    if series.function == DAILY:
        if series.adjustment_basis != ADJUSTMENT_DAILY_RAW or series.outputsize != COMPACT:
            raise ValidationError("mixed_adjustment", "daily series is not raw compact")
        if any(bar.adjusted_close is not None or bar.dividend_amount is not None for bar in series.bars):
            raise ValidationError("mixed_adjustment", "daily series carries an adjusted close")
    elif series.function == WEEKLY_ADJUSTED:
        if series.adjustment_basis != adjustment_basis(WEEKLY_ADJUSTED):
            raise ValidationError("mixed_adjustment", "weekly series has the wrong adjustment basis")
        if any(bar.adjusted_close is None or bar.dividend_amount is None for bar in series.bars):
            raise ValidationError("mixed_adjustment", "weekly series is missing adjusted close or dividend")
    else:
        raise ValidationError("mixed_adjustment", "unknown function")
