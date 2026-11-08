"""Redaction helpers. Live errors do not include response bodies; this is the backstop."""

from __future__ import annotations

import re

_APIKEY_QUERY = re.compile(r"(?i)(apikey\s*=\s*)[^&\s\"']+")
_APIKEY_FIELD = re.compile(r"(?i)(\"apikey\"\s*:\s*\")[^\"]+\"")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    out = _APIKEY_QUERY.sub(r"\1REDACTED", text)
    out = _APIKEY_FIELD.sub(r"\1REDACTED\"", out)
    for secret in secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "REDACTED")
    return out


def contains_secret(text: str, secrets: tuple[str, ...] = ()) -> bool:
    if _APIKEY_QUERY.search(text) or _APIKEY_FIELD.search(text):
        return True
    return any(secret and len(secret) >= 8 and secret in text for secret in secrets)
