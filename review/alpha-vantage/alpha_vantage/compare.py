"""Offline, timestamp-aligned comparison of two already loaded bar frames.

The result names venue, volume, and adjustment differences. It does not rank
the feeds and it does not produce a trading or profit conclusion. Adjusted
close, when present, is not compared as if it were the raw close.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .errors import LocalRefusal, ValidationError
from .spec import ALPACA_BAR_COLUMNS, NY

DISCLAIMER = (
    "Descriptive overlap only. Differences are labeled by venue, volume coverage, "
    "and adjustment basis. This report does not rank the series and does not "
    "estimate trading performance."
)


@dataclass(frozen=True)
class FeedSpec:
    name: str
    venue: str
    adjustment: str
    timeframe: str


def compare_sessions(left: pd.DataFrame, right: pd.DataFrame, left_spec: FeedSpec, right_spec: FeedSpec) -> dict:
    _require_spec(left_spec)
    _require_spec(right_spec)
    _agree(left, left_spec)
    _agree(right, right_spec)
    left_sessions = _by_session(left)
    right_sessions = _by_session(right)
    overlap = left_sessions.index.intersection(right_sessions.index)
    same_adjustment = left_spec.adjustment == right_spec.adjustment
    deltas = {}
    for column in ALPACA_BAR_COLUMNS:
        if len(overlap) == 0:
            deltas[column] = {"compared": 0, "max_abs": None, "median_abs": None}
            continue
        diff = (left_sessions.loc[overlap, column] - right_sessions.loc[overlap, column]).abs()
        deltas[column] = {
            "compared": int(len(overlap)),
            "max_abs": float(diff.max()),
            "median_abs": float(diff.median()),
        }
    return {
        "disclaimer": DISCLAIMER,
        "conclusion": "not_a_ranking",
        "close_column_is_the_only_close_compared": True,
        "adjusted_close_was_not_used_as_close": True,
        "left": _spec_dict(left_spec),
        "right": _spec_dict(right_spec),
        "adjustment_same": same_adjustment,
        "venue_same": left_spec.venue == right_spec.venue,
        "timeframe_same": left_spec.timeframe == right_spec.timeframe,
        "left_had_adjusted_close_column": "adjusted_close" in left.columns,
        "right_had_adjusted_close_column": "adjusted_close" in right.columns,
        "aligned_sessions": int(len(overlap)),
        "only_left": _dates(left_sessions.index.difference(right_sessions.index)),
        "only_right": _dates(right_sessions.index.difference(left_sessions.index)),
        "field_deltas": deltas,
        "volume_note": (
            "Volume is labeled, not scored. A single-venue print and a publisher "
            "daily volume can differ without either series being wrong."
        ),
        "adjustment_note": (
            "Adjustment bases match. A price delta is still not an accuracy score."
            if same_adjustment
            else "Adjustment bases differ. Price deltas mix conventions and are not an accuracy score. "
            "Raw OHLC was not rewritten to an adjusted close."
        ),
    }


def load_bar_file(path: Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(path)
    elif suffix == ".csv":
        frame = pd.read_csv(path)
    else:
        raise LocalRefusal("bar file must be .csv or .parquet")
    if "time" in getattr(frame, "columns", []):
        frame = frame.copy()
        frame["time"] = pd.to_datetime(frame["time"])
        frame = frame.set_index("time")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValidationError("bad_date", "bar file index is not a datetime index")
    frame.index.name = "time"
    return frame


def _by_session(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in ALPACA_BAR_COLUMNS if column not in frame.columns]
    if missing:
        raise ValidationError("illformed_ohlc", "frame is missing OHLC columns")
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        raise ValidationError("bad_date", "frame index is not a datetime index")
    try:
        local = index.tz_localize(NY) if index.tz is None else index.tz_convert(NY)
    except Exception:
        raise ValidationError("bad_date", "timestamps could not be placed on the New York clock") from None
    sessions = local.normalize()
    if sessions.has_duplicates:
        raise ValidationError("duplicate_date", "timestamps collapse to the same session")
    try:
        out = frame.loc[:, list(ALPACA_BAR_COLUMNS)].astype("float64").copy()
    except (TypeError, ValueError):
        raise ValidationError("illformed_ohlc", "OHLC columns are not numeric") from None
    out.index = sessions
    out.index.name = "session"
    return out.sort_index()


def _require_spec(spec: FeedSpec) -> None:
    if not isinstance(spec, FeedSpec):
        raise ValidationError("mixed_adjustment", "feed label is missing")
    for field in ("name", "venue", "adjustment", "timeframe"):
        value = getattr(spec, field)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("mixed_adjustment", "feed label is incomplete")


def _agree(frame: pd.DataFrame, spec: FeedSpec) -> None:
    labeled = frame.attrs.get("adjustment_basis")
    if labeled is not None and labeled != spec.adjustment:
        raise ValidationError("mixed_adjustment", "frame adjustment attribute does not match the supplied label")
    labeled_timeframe = frame.attrs.get("timeframe")
    if labeled_timeframe is not None and labeled_timeframe != spec.timeframe:
        raise ValidationError("mixed_adjustment", "frame timeframe attribute does not match the supplied label")


def _spec_dict(spec: FeedSpec) -> dict:
    return {"name": spec.name, "venue": spec.venue, "adjustment": spec.adjustment, "timeframe": spec.timeframe}


def _dates(index: pd.DatetimeIndex) -> dict:
    values = [stamp.date().isoformat() for stamp in index]
    return {"count": len(values), "dates": values[:20]}
