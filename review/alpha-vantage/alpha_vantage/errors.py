"""Errors whose text is safe to print. They never carry the API key or a query URL."""


class ReviewError(Exception):
    """Base for the review adapter. ``str(exc)`` is redacted."""


class OptInRequired(ReviewError):
    """Live calls stay off until the caller and the environment both opt in."""


class UnsafeLogging(ReviewError):
    """HTTP debug logging can record the query string, so the call is refused."""


class MissingKey(ReviewError):
    """The env var is unset. The message does not echo a value."""


class LocalRefusal(ReviewError):
    """The request is rejected before a provider call or a quota reservation."""


class QuotaExceeded(ReviewError):
    """This tool's local UTC-day ledger is already at 25 attempts."""


class RepeatAttemptRefused(ReviewError):
    """The same symbol and function was already attempted today. No retry."""


class LockTimeout(ReviewError):
    """The cross-process ledger lock was not acquired. Nothing was reserved."""


class NetworkError(ReviewError):
    """The HTTP call failed. The message has no URL and no response body."""


class HttpStatusError(ReviewError):
    """A non-200 status. The body is not included."""

    def __init__(self, status: int):
        self.status = int(status)
        super().__init__(f"http {self.status}")


class ProviderMessage(ReviewError):
    """HTTP 200 whose JSON is a note, an information message, or an error, not bars."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(f"provider returned {category} instead of bars")


class ValidationError(ReviewError):
    """The payload is not usable bars. ``code`` is a stable short reason."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        text = code if not detail else f"{code}: {detail}"
        super().__init__(text)


class StaleSeries(ValidationError):
    """The newest session is too old for the caller's as-of date."""

    def __init__(self, detail: str):
        super().__init__("stale", detail)


class CacheRejected(ReviewError):
    """A cache file exists but is not a trustworthy copy of the requested series."""
