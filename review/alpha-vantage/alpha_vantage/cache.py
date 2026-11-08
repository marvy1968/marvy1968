"""On-disk copies of validated bars, with the adjustment basis written beside them.

The file stores the parsed fields and provenance. It does not store a query
string. A later read runs the same checks as a live payload, including the
caller's as-of date, so a stale or relabeled file is not treated as fresh.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .errors import CacheRejected, StaleSeries, ValidationError
from .redact import contains_secret
from .spec import DAILY, HOST, NY, ROLE
from .validate import SeriesRequest, ValidatedSeries, validate_bars

_FORMAT = 1


def series_cache_path(root: Path, request: SeriesRequest) -> Path:
    size = request.outputsize or "none"
    path = (Path(root) / "series" / request.function / request.symbol / f"{size}.json").resolve()
    root_resolved = Path(root).resolve()
    if path != root_resolved and root_resolved not in path.parents:
        raise CacheRejected("cache path escaped the cache directory")
    return path


def inspect_cache(
    root: Path,
    request: SeriesRequest,
    as_of: datetime,
    max_calendar_age_days: int,
) -> tuple[str, ValidatedSeries | None, dict | None]:
    path = series_cache_path(root, request)
    if not path.exists():
        return "missing", None, None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        series = series_from_document(document, request, as_of, max_calendar_age_days)
    except StaleSeries:
        return "stale", None, None
    except (OSError, json.JSONDecodeError, CacheRejected, ValidationError):
        return "rejected", None, None
    recorded = document.get("provenance") if isinstance(document, dict) else None
    if not isinstance(recorded, dict):
        return "rejected", None, None
    return "fresh", series, recorded


def store_series(
    root: Path,
    series: ValidatedSeries,
    *,
    fetched_at: datetime,
    validated_as_of: datetime,
    secrets: tuple[str, ...] = (),
) -> dict:
    request = SeriesRequest(series.symbol, series.function, series.outputsize)
    document = {
        "format": _FORMAT,
        "provenance": provenance(series, fetched_at=fetched_at, validated_as_of=validated_as_of),
        "meta": _meta(series),
        "series": {bar.session_date.isoformat(): bar.provider_fields(series.function) for bar in series.bars},
    }
    # Re-run the bar checks on the document we are about to trust later.
    series_from_document(document, request, validated_as_of, max_calendar_age_days=30)
    text = json.dumps(document, indent=2, sort_keys=True)
    if contains_secret(text, secrets):
        raise ValidationError("secret_in_payload", "refusing to store a payload that contains a secret")
    path = series_cache_path(root, request)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, text)
    return document["provenance"]


def series_from_document(
    document: object,
    request: SeriesRequest,
    as_of: datetime,
    max_calendar_age_days: int,
) -> ValidatedSeries:
    if not isinstance(document, dict) or document.get("format") != _FORMAT:
        raise CacheRejected("cache format is not recognized")
    meta = document.get("meta")
    series_map = document.get("series")
    recorded = document.get("provenance")
    if not isinstance(meta, dict) or not isinstance(series_map, dict) or not isinstance(recorded, dict):
        raise CacheRejected("cache document is incomplete")
    series = validate_bars(meta, series_map, request, as_of, max_calendar_age_days)
    _require_provenance(recorded, series)
    return series


def provenance(series: ValidatedSeries, *, fetched_at: datetime, validated_as_of: datetime) -> dict:
    if fetched_at.tzinfo is None or validated_as_of.tzinfo is None:
        raise ValidationError("timezone", "provenance clocks must be timezone-aware")
    return {
        "provider": "alpha_vantage",
        "host": HOST,
        "function": series.function,
        "symbol": series.symbol,
        "outputsize": series.outputsize,
        "adjustment_basis": series.adjustment_basis,
        "ohlc_basis": series.ohlc_basis,
        "adjusted_close_basis": series.adjusted_close_basis,
        "feed": "not_an_alpaca_feed",
        "venue": "not_independently_verified",
        "timezone": "US/Eastern",
        "session_clock": NY,
        "fetched_at_utc": fetched_at.astimezone(timezone.utc).isoformat(),
        "validated_as_of": validated_as_of.isoformat(),
        "last_refreshed": series.last_refreshed.isoformat(),
        "oldest_session": series.oldest.isoformat(),
        "newest_session": series.newest.isoformat(),
        "bar_count": len(series.bars),
        "http_status": 200,
        "role": ROLE,
        "trading_feed": False,
        "premium_endpoint": False,
    }


def _require_provenance(recorded: dict, series: ValidatedSeries) -> None:
    expected = {
        "provider": "alpha_vantage",
        "host": HOST,
        "function": series.function,
        "symbol": series.symbol,
        "outputsize": series.outputsize,
        "adjustment_basis": series.adjustment_basis,
        "ohlc_basis": "raw_unadjusted",
        "adjusted_close_basis": series.adjusted_close_basis,
        "feed": "not_an_alpaca_feed",
        "venue": "not_independently_verified",
        "timezone": "US/Eastern",
        "session_clock": NY,
        "last_refreshed": series.last_refreshed.isoformat(),
        "oldest_session": series.oldest.isoformat(),
        "newest_session": series.newest.isoformat(),
        "bar_count": len(series.bars),
        "http_status": 200,
        "role": ROLE,
        "trading_feed": False,
        "premium_endpoint": False,
    }
    for key, value in expected.items():
        if recorded.get(key) != value:
            raise CacheRejected("cache provenance does not match the bars")
    if not isinstance(recorded.get("fetched_at_utc"), str) or not isinstance(recorded.get("validated_as_of"), str):
        raise CacheRejected("cache provenance timestamps are missing")


def _meta(series: ValidatedSeries) -> dict:
    zone_key = "5. Time Zone" if series.function == DAILY else "4. Time Zone"
    meta = {
        "1. Information": series.information,
        "2. Symbol": series.symbol,
        "3. Last Refreshed": series.last_refreshed.isoformat(),
        zone_key: "US/Eastern",
    }
    if series.function == DAILY:
        meta["4. Output Size"] = "Compact"
    return meta


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
