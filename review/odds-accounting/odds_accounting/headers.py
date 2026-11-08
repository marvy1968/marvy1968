"""Parse THE ODDS API usage headers.

x-requests-last is the per-response cost when it is a non-negative integer.
Zero is a real cost. A missing, blank, fractional, or non-numeric value is
not a cost. A negative value is not stored as a cost and is not rewritten
to zero.

x-requests-used and x-requests-remaining are account-wide snapshots. They
are never a single consumer's bill.
"""

from __future__ import annotations

import re

USAGE_HEADER_NAMES = ("x-requests-last", "x-requests-used", "x-requests-remaining")
# Odds API plans in this review are far below a billion credits. Longer digit
# strings are rejected before int() so a malformed header cannot allocate a
# huge integer. They are not stored and are not rewritten to zero.
MAX_DIGITS = 9
MAX_VALUE = 1_000_000_000


def extract_usage_headers(headers) -> dict | None:
    """Return only the three usage headers. Other header values are discarded."""
    if headers is None:
        return None
    try:
        items = list(headers.items())
    except Exception:
        return None
    found = {}
    for key, value in items:
        try:
            name = str(key).lower()
        except Exception:
            continue
        if name in USAGE_HEADER_NAMES and name not in found:
            found[name] = value
    return found


def parse_count(value) -> tuple[int | None, str]:
    """Return (integer, status). Status is header only for 0, 1, 2, ..."""
    if isinstance(value, bool) or isinstance(value, float):
        return None, "unparseable"
    if isinstance(value, int):
        if value < 0:
            return None, "negative_rejected"
        if value >= MAX_VALUE:
            return None, "out_of_range"
        return value, "header"
    if isinstance(value, bytes):
        if len(value) > MAX_DIGITS + 1:
            return None, "out_of_range"
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            return None, "unparseable"
    elif isinstance(value, str):
        text = value
    elif value is None:
        return None, "missing"
    else:
        return None, "unparseable"
    if len(text) > 64:
        return None, "out_of_range"
    text = text.strip()
    if text == "":
        return None, "missing"
    if text.startswith("-"):
        rest = text[1:]
        if len(rest) > MAX_DIGITS:
            return None, "out_of_range"
        if rest.isdigit():
            return None, "negative_rejected"
        return None, "unparseable"
    if re.fullmatch(r"\d+", text) is None:
        return None, "unparseable"
    if len(text) > MAX_DIGITS:
        return None, "out_of_range"
    number = int(text)
    if number >= MAX_VALUE:
        return None, "out_of_range"
    return number, "header"


def cost_basis(actual_status: str, estimated_cost: int | None, http_status: int | None,
               transport_failed: bool) -> str:
    """Label how the row may be read. Header actual is the only actual cost."""
    if actual_status == "header":
        return "header"
    if actual_status in ("unparseable", "out_of_range", "negative_rejected"):
        return actual_status
    if actual_status == "legacy_unlabeled":
        return "legacy_unlabeled_not_actual"
    if transport_failed or http_status is None:
        return "unknown"
    if http_status != 200:
        if estimated_cost not in (None, 0):
            return "unknown_never_assert_zero"
        return "unknown"
    if estimated_cost is not None:
        return "estimate_only_not_actual"
    return "unknown"
