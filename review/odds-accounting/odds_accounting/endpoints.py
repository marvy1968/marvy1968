"""Turn a path into a template. Concrete event ids and queries are not kept."""

from __future__ import annotations

import re

_RULES = (
    (re.compile(r"^/(?:v4/)?historical/sports/([A-Za-z0-9_]{1,64})/events/[^/]+/odds$"),
     "/v4/historical/sports/{sport}/events/{event}/odds", True),
    (re.compile(r"^/(?:v4/)?historical/sports/([A-Za-z0-9_]{1,64})/events$"),
     "/v4/historical/sports/{sport}/events", True),
    (re.compile(r"^/(?:v4/)?sports/([A-Za-z0-9_]{1,64})/events/[^/]+/odds$"),
     "/v4/sports/{sport}/events/{event}/odds", False),
    (re.compile(r"^/(?:v4/)?sports/([A-Za-z0-9_]{1,64})/events$"),
     "/v4/sports/{sport}/events", False),
    (re.compile(r"^/(?:v4/)?sports/([A-Za-z0-9_]{1,64})/odds$"),
     "/v4/sports/{sport}/odds", False),
)

_CANONICAL = {template for _pattern, template, _historical in _RULES}


def classify_endpoint(path: str | None, template: str | None = None,
                      sport: str | None = None, historical: bool | None = None):
    """Return template, sport, historical, accepted.

    A path that contains a query, a scheme, or an unexpected shape is rejected.
    The rejected text is not returned.
    """
    if isinstance(template, str) and template in _CANONICAL:
        hist = historical if isinstance(historical, bool) else ("/historical/" in template)
        kept_sport = sport if isinstance(sport, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", sport) else None
        return template, kept_sport, hist, True
    if not isinstance(path, str):
        return None, None, False, False
    text = path.strip()
    if not text or "?" in text or "://" in text or "#" in text or "apiKey" in text or " " in text:
        return None, None, False, False
    if text.startswith("https://") or text.startswith("http://"):
        return None, None, False, False
    for pattern, canon, hist in _RULES:
        match = pattern.fullmatch(text)
        if match:
            return canon, match.group(1), hist, True
    return None, None, False, False
