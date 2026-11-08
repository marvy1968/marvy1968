"""Offline research commands, plus one explicitly opted-in fetch.

Examples, from a checkout, with no effect on alpacabot:

    PYTHONPATH=review/alpha-vantage python -m alpha_vantage validate-fixture --json bars.json --symbol TEST --function TIME_SERIES_DAILY --as-of 2026-11-08T12:00:00-05:00
    PYTHONPATH=review/alpha-vantage python -m alpha_vantage compare --av-response bars.json --iex iex.csv --symbol TEST --function TIME_SERIES_DAILY --as-of 2026-11-08T12:00:00-05:00 --iex-adjustment alpaca_adjustment_all --iex-venue iex --iex-timeframe 1Day

A live call also needs ``--opt-in`` and ``ALPHA_VANTAGE_RESEARCH_OPT_IN=1``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .compare import FeedSpec, compare_sessions, load_bar_file
from .errors import (
    CacheRejected,
    HttpStatusError,
    LocalRefusal,
    LockTimeout,
    NetworkError,
    ProviderMessage,
    QuotaExceeded,
    RepeatAttemptRefused,
    ReviewError,
    StaleSeries,
    ValidationError,
)
from .research import caveat, load_series, offline_from_payload
from .spec import DAILY, DEFAULT_MAX_CALENDAR_AGE_DAYS, WEEKLY_ADJUSTED
from .validate import parse_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpha_vantage", description="Review-only Alpha Vantage research helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    offline = sub.add_parser("validate-fixture", help="validate a local JSON payload and print its basis")
    _add_series_args(offline)
    offline.add_argument("--json", required=True, type=Path)

    compare = sub.add_parser("compare", help="label differences against a local IEX or Alpaca bar file")
    _add_series_args(compare)
    source = compare.add_mutually_exclusive_group(required=True)
    source.add_argument("--av-response", type=Path)
    source.add_argument("--cache-file", type=Path)
    compare.add_argument("--iex", required=True, type=Path)
    compare.add_argument("--iex-name", default="supplied_iex")
    compare.add_argument("--iex-venue", required=True)
    compare.add_argument("--iex-adjustment", required=True)
    compare.add_argument("--iex-timeframe", required=True)

    fetch = sub.add_parser("fetch", help="reserve one live call when opted in")
    _add_series_args(fetch)
    fetch.add_argument("--cache-dir", required=True, type=Path)
    fetch.add_argument("--opt-in", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-fixture":
            return _validate(args)
        if args.command == "compare":
            return _compare(args)
        if args.command == "fetch":
            return _fetch(args)
    except ReviewError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return _exit_code(exc)
    print("unknown command", file=sys.stderr)
    return 2


def _add_series_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--function", required=True, choices=(DAILY, WEEKLY_ADJUSTED))
    parser.add_argument("--outputsize", default=None)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--max-calendar-age-days", type=int, default=DEFAULT_MAX_CALENDAR_AGE_DAYS)


def _validate(args) -> int:
    result = offline_from_payload(
        _read_json(args.json),
        symbol=args.symbol,
        function=args.function,
        outputsize=_outputsize(args),
        as_of=_clock(args.as_of),
        max_calendar_age_days=args.max_calendar_age_days,
    )
    _print_summary(result)
    return 0


def _compare(args) -> int:
    as_of = _clock(args.as_of)
    outputsize = _outputsize(args)
    if args.av_response is not None:
        result = offline_from_payload(
            _read_json(args.av_response),
            symbol=args.symbol,
            function=args.function,
            outputsize=outputsize,
            as_of=as_of,
            max_calendar_age_days=args.max_calendar_age_days,
        )
    else:
        from .cache import series_from_document

        request = parse_request(args.symbol, args.function, outputsize)
        document = _read_json(args.cache_file)
        series = series_from_document(document, request, as_of, args.max_calendar_age_days)
        recorded = dict(document["provenance"])
        recorded["source"] = "cache_file"
        from .research import _pack

        result = _pack(series, recorded, cache_hit=True, network_calls=0)
    report = compare_sessions(
        result.raw_ohlcv,
        load_bar_file(args.iex),
        FeedSpec(
            name="alpha_vantage",
            venue=str(result.raw_ohlcv.attrs.get("venue") or "not_independently_verified"),
            adjustment=str(result.raw_ohlcv.attrs["adjustment_basis"]),
            timeframe=str(result.raw_ohlcv.attrs["timeframe"]),
        ),
        FeedSpec(
            name=args.iex_name,
            venue=args.iex_venue,
            adjustment=args.iex_adjustment,
            timeframe=args.iex_timeframe,
        ),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _fetch(args) -> int:
    print(caveat())
    if not args.opt_in:
        print("OptInRequired: pass --opt-in as well as ALPHA_VANTAGE_RESEARCH_OPT_IN=1", file=sys.stderr)
        return 2
    result = load_series(
        symbol=args.symbol,
        function=args.function,
        outputsize=_outputsize(args),
        cache_dir=args.cache_dir,
        as_of=_clock(args.as_of),
        opt_in=True,
        max_calendar_age_days=args.max_calendar_age_days,
    )
    _print_summary(result)
    return 0


def _print_summary(result) -> None:
    differed = None
    if result.weekly_research is not None:
        differed = int((result.weekly_research["adjusted_close"] != result.weekly_research["close"]).sum())
    print(
        json.dumps(
            {
                "symbol": result.series.symbol,
                "function": result.series.function,
                "bars": len(result.series.bars),
                "oldest": result.series.oldest.isoformat(),
                "newest": result.series.newest.isoformat(),
                "adjustment_basis": result.series.adjustment_basis,
                "ohlc_basis": result.series.ohlc_basis,
                "adjusted_close_basis": result.series.adjusted_close_basis,
                "feed": result.provenance.get("feed"),
                "venue": result.provenance.get("venue"),
                "fetched_at_utc": result.provenance.get("fetched_at_utc"),
                "cache_hit": result.cache_hit,
                "network_calls": result.network_calls,
                "alpaca_layout": list(result.raw_ohlcv.columns),
                "timeframe": result.raw_ohlcv.attrs.get("timeframe"),
                "weekly_rows_where_adjusted_close_differs_from_close": differed,
                "trading_feed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _outputsize(args) -> str | None:
    if args.function == DAILY:
        return args.outputsize or "compact"
    if args.outputsize is not None:
        raise LocalRefusal("weekly adjusted does not take an output size")
    return None


def _clock(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError("bad_date", "as-of is not an ISO timestamp") from exc
    if value.tzinfo is None:
        raise ValidationError("timezone", "as-of must include a time zone offset")
    return value


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _exit_code(exc: ReviewError) -> int:
    from .errors import HttpStatusError, LockTimeout, NetworkError, ProviderMessage, QuotaExceeded, RepeatAttemptRefused

    if isinstance(exc, (QuotaExceeded, RepeatAttemptRefused, LockTimeout)):
        return 4
    if isinstance(exc, (NetworkError, HttpStatusError, ProviderMessage)):
        return 5
    if isinstance(exc, (ValidationError, CacheRejected, StaleSeries)):
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
