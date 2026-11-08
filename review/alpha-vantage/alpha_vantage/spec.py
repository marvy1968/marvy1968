"""Closed contract for the observed free endpoints.

The evaluation's support page states a standard free allowance of 25 requests
per day. It does not state a per-minute cap or the provider's reset clock.
This package therefore counts attempts in its own UTC-day ledger and does not
invent a minute scheduler. The ledger cannot see other programs using the key.
"""

from __future__ import annotations

import re

DAILY = "TIME_SERIES_DAILY"
WEEKLY_ADJUSTED = "TIME_SERIES_WEEKLY_ADJUSTED"
COMPACT = "compact"

ALLOWED = {
    DAILY: COMPACT,
    WEEKLY_ADJUSTED: None,
}

FREE_DAILY_QUOTA = 25
DEFAULT_MAX_CALENDAR_AGE_DAYS = 4
MAX_CALENDAR_AGE_DAYS = 30
HTTP_TIMEOUT_SECONDS = 20.0
HOST = "www.alphavantage.co"
ENDPOINT = f"https://{HOST}/query"

NY = "America/New_York"
ALPACA_BAR_COLUMNS = ("open", "high", "low", "close", "volume")
ALPACA_INDEX_NAME = "time"

ADJUSTMENT_DAILY_RAW = "raw_as_traded_ohlcv"
ADJUSTMENT_WEEKLY = "raw_ohlc_volume_plus_adjusted_close_and_dividend"
OHLC_BASIS = "raw_unadjusted"
ADJUSTED_CLOSE_DAILY = "not_applicable"
ADJUSTED_CLOSE_WEEKLY = "splits_and_cash_dividends_close_only"

SERIES_KEY = {
    DAILY: "Time Series (Daily)",
    WEEKLY_ADJUSTED: "Weekly Adjusted Time Series",
}

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

SHARED_QUOTA_CAVEAT = (
    "This tool reserves calls against its own UTC-day ledger, capped at the documented "
    "standard free allowance of 25 requests per day. Other uses of the same key are unknown, "
    "so a free slot in this ledger does not promise that the provider quota is free. "
    "Failed attempts stay counted. This tool does not retry and does not call premium endpoints. "
    "It is an optional end-of-day reconciliation and weekly research helper, not a realtime "
    "fallback, not an automatic trading feed, and not a deep daily backtest source."
)

ROLE = "optional_eod_reconciliation_or_weekly_research"


def adjustment_basis(function: str) -> str:
    if function == DAILY:
        return ADJUSTMENT_DAILY_RAW
    if function == WEEKLY_ADJUSTED:
        return ADJUSTMENT_WEEKLY
    raise ValueError(function)


def adjusted_close_basis(function: str) -> str:
    if function == DAILY:
        return ADJUSTED_CLOSE_DAILY
    if function == WEEKLY_ADJUSTED:
        return ADJUSTED_CLOSE_WEEKLY
    raise ValueError(function)
