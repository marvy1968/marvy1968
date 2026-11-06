"""Retries with backoff. A timed-out order is looked up before it is sent again."""

from __future__ import annotations

from .errors import BrokerReadError


def is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    markers = ("timeout", "timed out", "connection", "429", "502", "503", "504", "500")
    return any(marker in name or marker in text for marker in markers)


def call_read(fn, retries: int, backoff: tuple[float, ...], sleep):
    last: BaseException | None = None
    attempts = max(1, retries)
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if not is_retryable(exc):
                raise BrokerReadError(type(exc).__name__) from exc
            last = exc
            if i == attempts - 1:
                break
            sleep(backoff[min(i, len(backoff) - 1)])
    raise BrokerReadError(type(last).__name__ if last else "read")


def call_submit(submit, find, retries: int, backoff: tuple[float, ...], sleep):
    """On timeout, check the order. Resubmit only when the status check says it is absent.

    If the status check itself fails, do not send the order again.
    """
    last: BaseException | None = None
    attempts = max(1, retries)
    for i in range(attempts):
        try:
            return submit()
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            duplicate = "duplicate" in str(exc).lower() or "client_order_id" in str(exc).lower()
            if not is_retryable(exc) and not duplicate:
                raise
            try:
                found = find()
            except Exception as find_exc:
                raise BrokerReadError(f"status check failed: {type(find_exc).__name__}") from find_exc
            if found is not None:
                return found
            if not is_retryable(exc):
                raise
            last = exc
            if i == attempts - 1:
                break
            sleep(backoff[min(i, len(backoff) - 1)])
    raise BrokerReadError(type(last).__name__ if last else "submit")


def force_timeouts(client, connect_sec: float, read_sec: float) -> None:
    """Put a hard connect/read timeout on every request the client's session makes."""
    session = getattr(client, "_session", None)
    if session is None or not hasattr(session, "request"):
        raise BrokerReadError("client has no HTTP session")
    original = session.request
    timeout = (float(connect_sec), float(read_sec))

    def request(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(method, url, **kwargs)

    session.request = request
