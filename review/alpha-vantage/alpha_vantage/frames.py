"""Alpaca-shaped views of raw OHLC, kept apart from adjusted close.

``alpacabot.data`` stores float columns open, high, low, close, volume on a
DatetimeIndex named ``time`` in America/New_York. Daily timestamps there are
the session converted into that zone. This module builds the same layout from
raw OHLC only. Adjusted close is never copied into ``close``.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from .errors import ValidationError
from .spec import ADJUSTMENT_DAILY_RAW, ALPACA_BAR_COLUMNS, ALPACA_INDEX_NAME, DAILY, NY, WEEKLY_ADJUSTED
from .validate import ValidatedSeries, assert_basis

_LAYOUT_NOTE = (
    "Columns and the America/New_York time index follow the alpacabot bar layout. "
    "Values are raw OHLC and volume. alpacabot.data requests adjustment=all, so this "
    "frame is not a substitute for that adjusted history."
)


def alpaca_raw_ohlcv(series: ValidatedSeries) -> pd.DataFrame:
    """Five-column raw frame. Weekly adjusted close is left out on purpose."""
    assert_basis(series)
    index = _session_index(bar.session_date for bar in series.bars)
    frame = pd.DataFrame(
        {
            "open": [float(bar.open) for bar in series.bars],
            "high": [float(bar.high) for bar in series.bars],
            "low": [float(bar.low) for bar in series.bars],
            "close": [float(bar.close) for bar in series.bars],
            "volume": [float(bar.volume) for bar in series.bars],
        },
        index=index,
    ).astype("float64")
    timeframe = "1Day" if series.function == DAILY else "1Week"
    _stamp_raw(frame, series, timeframe)
    if series.function == WEEKLY_ADJUSTED:
        # This view has no adjusted-close column. Say so in the label the
        # comparison uses, and keep the series-level basis beside it.
        frame.attrs["series_adjustment_basis"] = series.adjustment_basis
        frame.attrs["adjustment_basis"] = "raw_unadjusted_ohlcv"
        frame.attrs["adjusted_close_omitted"] = True
    problems = alpaca_layout_problems(frame)
    if problems:
        raise ValidationError("illformed_ohlc", ", ".join(problems))
    return frame


def weekly_research_frame(series: ValidatedSeries) -> pd.DataFrame:
    """Raw weekly OHLC plus adjusted close and dividend as their own columns."""
    if series.function != WEEKLY_ADJUSTED:
        raise ValidationError("mixed_adjustment", "weekly research frame requires TIME_SERIES_WEEKLY_ADJUSTED")
    raw = alpaca_raw_ohlcv(series)
    frame = raw.copy()
    frame["adjusted_close"] = [float(bar.adjusted_close) for bar in series.bars]
    frame["dividend_amount"] = [float(bar.dividend_amount) for bar in series.bars]
    frame = frame.astype("float64")
    frame.attrs = dict(raw.attrs)
    frame.attrs["adjustment_basis"] = series.adjustment_basis
    frame.attrs["adjusted_close_basis"] = series.adjusted_close_basis
    frame.attrs["adjusted_close_omitted"] = False
    frame.attrs["raw_ohlc_not_adjusted"] = True
    frame.attrs["do_not_replace_close_with_adjusted_close"] = True
    if list(frame.columns)[:5] != list(ALPACA_BAR_COLUMNS):
        raise ValidationError("mixed_adjustment", "weekly research columns were reordered")
    return frame


def aggregate_raw_daily_to_weeks(frame: pd.DataFrame) -> pd.DataFrame:
    """Last-session-of-week raw OHLC from daily bars. Adjusted close is not computed.

    The week label is the last weekday present in that Friday-ending week, which is
    the labeling convention of the weekly series. This is an internal aggregation,
    not a second provider and not a corporate-action adjustment.
    """
    problems = alpaca_layout_problems(frame)
    if problems:
        raise ValidationError("illformed_ohlc", ", ".join(problems))
    if "adjusted_close" in frame.columns:
        raise ValidationError("mixed_adjustment", "refusing to aggregate a frame that carries adjusted close")
    basis = frame.attrs.get("adjustment_basis")
    if basis not in (None, ADJUSTMENT_DAILY_RAW):
        raise ValidationError("mixed_adjustment", "refusing to aggregate bars that are not labeled raw daily")
    timeframe = frame.attrs.get("timeframe")
    if timeframe not in (None, "1Day"):
        raise ValidationError("mixed_adjustment", "weekly aggregation expects daily bars")

    dates = [ts.date() for ts in _as_ny(frame.index)]
    groups: dict = {}
    for position, session in enumerate(dates):
        if session.weekday() >= 5:
            raise ValidationError("weekend_date", session.isoformat())
        week_friday = session + timedelta(days=(4 - session.weekday()))
        groups.setdefault(week_friday, []).append(position)

    rows = []
    labels = []
    for week_friday in sorted(groups):
        positions = sorted(groups[week_friday], key=lambda i: dates[i])
        chunk = frame.iloc[positions]
        rows.append(
            {
                "open": float(chunk["open"].iloc[0]),
                "high": float(chunk["high"].max()),
                "low": float(chunk["low"].min()),
                "close": float(chunk["close"].iloc[-1]),
                "volume": float(chunk["volume"].sum()),
            }
        )
        labels.append(dates[positions[-1]])
    out = pd.DataFrame(rows, index=_session_index(labels)).astype("float64")
    out.attrs["provider"] = "internal_weekly_aggregation"
    out.attrs["timeframe"] = "1Week"
    out.attrs["ohlc_basis"] = "raw_unadjusted"
    out.attrs["adjustment_basis"] = "raw_weekly_ohlcv_aggregated_from_daily"
    out.attrs["adjusted_close_basis"] = "not_computed"
    out.attrs["input_adjustment_basis"] = basis or "unlabeled"
    out.attrs["layout_note"] = _LAYOUT_NOTE
    return out


def alpaca_layout_problems(frame: pd.DataFrame) -> list[str]:
    problems = []
    columns = list(frame.columns)
    if columns[: len(ALPACA_BAR_COLUMNS)] != list(ALPACA_BAR_COLUMNS):
        problems.append("columns")
    if frame.index.name != ALPACA_INDEX_NAME:
        problems.append("index name")
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        problems.append("index type")
        return problems
    tz = index.tz
    zone = str(tz) if tz is not None else ""
    key = getattr(tz, "key", None) or getattr(tz, "zone", None)
    if zone != NY and key != NY:
        problems.append("timezone")
    if not index.is_monotonic_increasing:
        problems.append("order")
    if index.has_duplicates:
        problems.append("duplicate timestamps")
    for column in ALPACA_BAR_COLUMNS:
        if column in frame.columns and str(frame[column].dtype) != "float64":
            problems.append(f"dtype {column}")
    return problems


def _stamp_raw(frame: pd.DataFrame, series: ValidatedSeries, timeframe: str) -> None:
    frame.attrs["provider"] = "alpha_vantage"
    frame.attrs["symbol"] = series.symbol
    frame.attrs["function"] = series.function
    frame.attrs["outputsize"] = series.outputsize
    frame.attrs["timeframe"] = timeframe
    frame.attrs["adjustment_basis"] = series.adjustment_basis
    frame.attrs["ohlc_basis"] = series.ohlc_basis
    frame.attrs["adjusted_close_basis"] = series.adjusted_close_basis
    frame.attrs["feed"] = "not_an_alpaca_feed"
    frame.attrs["venue"] = "not_independently_verified"
    frame.attrs["timezone"] = series.timezone
    frame.attrs["session_clock"] = NY
    frame.attrs["role"] = "optional_eod_reconciliation_or_weekly_research"
    frame.attrs["trading_feed"] = False
    frame.attrs["layout_note"] = _LAYOUT_NOTE
    frame.attrs["raw_ohlc_not_adjusted"] = True


def _session_index(sessions) -> pd.DatetimeIndex:
    stamps = [pd.Timestamp(session) for session in sessions]
    index = pd.DatetimeIndex(stamps)
    index = index.tz_localize(NY)
    index.name = ALPACA_INDEX_NAME
    return index


def _as_ny(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        return index.tz_localize(NY)
    return index.tz_convert(NY)
