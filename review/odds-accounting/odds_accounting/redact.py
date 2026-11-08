"""Drop values that must not enter the observe-only ledger."""

from __future__ import annotations

import re

TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
HEX_SECRET = re.compile(r"^[0-9a-fA-F]{32,}$")
SENSITIVE_WORD = re.compile(r"(?i)token|secret|password|apikey|api_key|api-key|bearer|authorization|credential")
LEAK = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|bearer|authorization)\s*[:=]|[?&][a-z0-9_]+\s*=|://"
)
SOURCE_JOB = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HEX_ID = re.compile(r"^[0-9a-f]{32}$")

FORBIDDEN_ARGUMENT_NAMES = frozenset({
    "body", "response", "response_body", "url", "params", "query", "api_key",
    "apikey", "headers_raw", "note", "exception", "exception_text", "secret",
    "token", "authorization", "raw", "key", "credential", "credentials",
})


def safe_token(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (not text or LEAK.search(text) or SENSITIVE_WORD.search(text) or HEX_SECRET.fullmatch(text)
            or TOKEN.fullmatch(text) is None):
        return None
    return text


def safe_tokens(value, limit: int = 32) -> tuple[list[str], bool]:
    """Return (kept, dropped_any). Strings are split on commas, not on characters."""
    if value is None:
        return [], False
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return [], True
    kept = []
    dropped = False
    for part in parts:
        if isinstance(part, str):
            token = safe_token(part)
            blank = part.strip() == ""
        elif isinstance(part, int) and not isinstance(part, bool):
            token = safe_token(str(part))
            blank = False
        else:
            token = None
            blank = False
        if token is None:
            if not blank:
                dropped = True
            continue
        kept.append(token)
        if len(kept) >= limit:
            if len(parts) > len(kept):
                dropped = True
            break
    return kept, dropped


def safe_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if HEX_ID.fullmatch(text) is None:
        return None
    return text


def exception_types(exc: BaseException | None) -> tuple[str | None, str | None]:
    """Class names only. Messages, URLs, and causes' messages are not read."""
    if not isinstance(exc, BaseException):
        return None, None
    names = []
    seen = set()
    current = exc
    while isinstance(current, BaseException) and len(names) < 2:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        name = type(current).__name__
        safe_name = name if name.isidentifier() and len(name) <= 80 and SENSITIVE_WORD.search(name) is None else "Exception"
        names.append(safe_name)
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    primary = names[0] if names else None
    cause = names[1] if len(names) > 1 else None
    return primary, cause
