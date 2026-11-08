"""One HTTP GET, and only for an already validated free request.

The key is read here, placed in the request parameters, and not written to an
exception, a log line, or a returned URL. Failures are not retried. Non-200
bodies are discarded. Debug logging on the HTTP libraries is refused because
those loggers can record the query string.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from .errors import HttpStatusError, MissingKey, NetworkError, UnsafeLogging, ValidationError
from .spec import ENDPOINT, HTTP_TIMEOUT_SECONDS
from .validate import SeriesRequest

SessionGet = Callable[[str, dict, float], tuple[int, bytes]]


def read_api_key(environ: dict) -> str:
    raw = environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not isinstance(raw, str) or not raw.strip():
        raise MissingKey("ALPHA_VANTAGE_API_KEY is not set")
    return raw.strip()


def key_is_present(environ: dict) -> bool:
    raw = environ.get("ALPHA_VANTAGE_API_KEY", "")
    return isinstance(raw, str) and bool(raw.strip())


def debug_logging_would_leak() -> bool:
    urllib3_log = logging.getLogger("urllib3").isEnabledFor(logging.DEBUG)
    pool_log = logging.getLogger("urllib3.connectionpool").isEnabledFor(logging.DEBUG)
    requests_log = logging.getLogger("requests").isEnabledFor(logging.DEBUG)
    return urllib3_log or pool_log or requests_log


def build_params(request: SeriesRequest, key: str) -> dict:
    params = {
        "function": request.function,
        "symbol": request.symbol,
        "datatype": "json",
        "apikey": key,
    }
    if request.outputsize is not None:
        params["outputsize"] = request.outputsize
    return params


def public_params(request: SeriesRequest) -> dict:
    """Parameters safe to pass a test double. The key is not included."""
    params = {"function": request.function, "symbol": request.symbol, "datatype": "json"}
    if request.outputsize is not None:
        params["outputsize"] = request.outputsize
    return params


def http_get(
    request: SeriesRequest,
    environ: dict,
    *,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    session_get: SessionGet | None = None,
) -> dict:
    key = read_api_key(environ)
    if debug_logging_would_leak():
        raise UnsafeLogging("HTTP debug logging is enabled; refusing to send the key")
    getter = session_get or _default_get
    try:
        status, content = getter(ENDPOINT, build_params(request, key), timeout)
    except NetworkError:
        raise
    except Exception:
        raise NetworkError("request failed before a validated payload was received") from None
    return decode_response(status, content)


def decode_response(status: object, content: object) -> dict:
    if not isinstance(status, int) or isinstance(status, bool) or not isinstance(content, (bytes, bytearray)):
        raise NetworkError("request failed before a validated payload was received")
    if status != 200:
        raise HttpStatusError(status)
    try:
        payload = json.loads(bytes(content).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ValidationError("non_object", "response was not json") from None
    if not isinstance(payload, dict):
        raise ValidationError("non_object", "response was not a JSON object")
    return payload


def _default_get(url: str, params: dict, timeout: float) -> tuple[int, bytes]:
    if url != ENDPOINT:
        raise NetworkError("refusing unexpected host")
    import requests

    try:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(max_retries=0)
        session.mount("https://", adapter)
        response = session.get(url, params=params, timeout=timeout)
        return response.status_code, response.content
    except Exception:
        raise NetworkError("request failed before a validated payload was received") from None
