"""Opt-in research entrypoint.

A fresh cache is returned with zero network calls. Anything else stays local
unless ``opt_in`` is true and ``ALPHA_VANTAGE_RESEARCH_OPT_IN`` is exactly ``1``.
One reserved attempt is one provider call. Failure does not retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import os

import pandas as pd

from . import client
from .cache import inspect_cache, provenance, store_series
from .errors import (
    CacheRejected,
    HttpStatusError,
    LocalRefusal,
    MissingKey,
    NetworkError,
    OptInRequired,
    ProviderMessage,
    StaleSeries,
    UnsafeLogging,
    ValidationError,
)
from .frames import alpaca_raw_ohlcv, weekly_research_frame
from .quota import QuotaLedger
from .spec import DEFAULT_MAX_CALENDAR_AGE_DAYS, HTTP_TIMEOUT_SECONDS, SHARED_QUOTA_CAVEAT, WEEKLY_ADJUSTED
from .validate import SeriesRequest, ValidatedSeries, parse_request, validate_payload


@dataclass(frozen=True)
class ResearchResult:
    series: ValidatedSeries
    raw_ohlcv: pd.DataFrame
    weekly_research: pd.DataFrame | None
    provenance: dict
    cache_hit: bool
    network_calls: int


def offline_from_payload(
    payload: dict,
    *,
    symbol: str,
    function: str,
    outputsize: str | None,
    as_of: datetime,
    max_calendar_age_days: int = DEFAULT_MAX_CALENDAR_AGE_DAYS,
    fetched_at: datetime | None = None,
) -> ResearchResult:
    """Validate a local payload. Does not read the key, the ledger, or the network."""
    request = parse_request(symbol, function, outputsize)
    series = validate_payload(payload, request, as_of, max_calendar_age_days)
    clock = fetched_at or as_of
    recorded = provenance(series, fetched_at=clock, validated_as_of=as_of)
    recorded["source"] = "local_fixture"
    return _pack(series, recorded, cache_hit=False, network_calls=0)


def load_series(
    *,
    symbol: str,
    function: str,
    outputsize: str | None,
    cache_dir,
    as_of: datetime,
    opt_in: bool = False,
    environ: dict | None = None,
    session_get=None,
    allow_repeat: bool = False,
    max_calendar_age_days: int = DEFAULT_MAX_CALENDAR_AGE_DAYS,
    now: datetime | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> ResearchResult:
    """Return cached bars, or reserve exactly one live call when explicitly opted in."""
    request = parse_request(symbol, function, outputsize)
    env = environ if environ is not None else os.environ
    state, cached, recorded = inspect_cache(cache_dir, request, as_of, max_calendar_age_days)
    if state == "fresh" and cached is not None and recorded is not None:
        recorded = dict(recorded)
        recorded["source"] = "cache"
        return _pack(cached, recorded, cache_hit=True, network_calls=0)
    if not _live_allowed(opt_in, env):
        if state == "stale":
            raise StaleSeries("cached series is stale and live refresh is disabled")
        if state == "rejected":
            raise CacheRejected("cached series was rejected and live refresh is disabled")
        raise OptInRequired("live Alpha Vantage calls are disabled")
    if client.debug_logging_would_leak():
        raise UnsafeLogging("HTTP debug logging is enabled; refusing to send the key")
    secrets: tuple[str, ...] = ()
    if session_get is None:
        if not client.key_is_present(env):
            raise MissingKey("ALPHA_VANTAGE_API_KEY is not set")
        secrets = (client.read_api_key(env),)
    moment = _aware(now or datetime.now(timezone.utc))
    ledger = QuotaLedger(cache_dir)
    reservation = ledger.reserve(request, now=moment, allow_repeat=allow_repeat)
    try:
        payload = _request(request, env, timeout=timeout, session_get=session_get)
        series = validate_payload(payload, request, as_of, max_calendar_age_days)
        recorded = store_series(
            cache_dir,
            series,
            fetched_at=moment,
            validated_as_of=as_of,
            secrets=secrets,
        )
    except Exception as exc:
        ledger.complete(reservation.token, _outcome(exc), now=moment)
        raise
    ledger.complete(reservation.token, "success", now=moment)
    recorded = dict(recorded)
    recorded["source"] = "network"
    return _pack(series, recorded, cache_hit=False, network_calls=1)


def caveat() -> str:
    return SHARED_QUOTA_CAVEAT


def _request(request: SeriesRequest, environ: dict, *, timeout: float, session_get) -> dict:
    if session_get is None:
        return client.http_get(request, environ, timeout=timeout)
    try:
        status, content = session_get(client.ENDPOINT, client.public_params(request), timeout)
    except client.NetworkError:
        raise
    except Exception:
        raise NetworkError("request failed before a validated payload was received") from None
    return client.decode_response(status, content)


def _pack(series: ValidatedSeries, recorded: dict, *, cache_hit: bool, network_calls: int) -> ResearchResult:
    raw = alpaca_raw_ohlcv(series)
    weekly = weekly_research_frame(series) if series.function == WEEKLY_ADJUSTED else None
    recorded = dict(recorded)
    recorded["cache_hit"] = cache_hit
    recorded["network_calls"] = network_calls
    raw.attrs["fetched_at_utc"] = recorded.get("fetched_at_utc")
    raw.attrs["cache_hit"] = cache_hit
    if weekly is not None:
        weekly.attrs["fetched_at_utc"] = recorded.get("fetched_at_utc")
        weekly.attrs["cache_hit"] = cache_hit
    return ResearchResult(series, raw, weekly, recorded, cache_hit, network_calls)


def _live_allowed(opt_in: bool, environ: dict) -> bool:
    return bool(opt_in) and environ.get("ALPHA_VANTAGE_RESEARCH_OPT_IN") == "1"


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LocalRefusal("clock must be a timezone-aware datetime")
    return value


def _outcome(exc: BaseException) -> str:
    if isinstance(exc, NetworkError):
        return "network"
    if isinstance(exc, HttpStatusError):
        return "http_status"
    if isinstance(exc, ProviderMessage):
        return "provider_message"
    if isinstance(exc, ValidationError):
        return "validation"
    return "failure"
