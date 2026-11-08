"""Odds transport boundary from the prop-shadow collector.

The reservation, reconcile, exception, and replay-environment behavior matches
the VM collector. The only addition is an observe-only note that cannot change
those outcomes. Replay children still receive no provider credential.
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlencode

ODDS_BASE = "https://api.the-odds-api.com/v4"

# Inherited process environment is not a safe base. Replay does not need a
# provider key or a chat token. Only these names may be copied, and the send
# flag is set here rather than taken from the parent.
REPLAY_ENV_ALLOW = (
    "PATH",
    "LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE",
    "PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONSAFEPATH",
    "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED",
    "PYTHONUNBUFFERED", "VIRTUAL_ENV", "LD_LIBRARY_PATH", "TMPDIR", "TZ",
)


class CollectorError(RuntimeError):
    pass


def _safe_odds_error(status: int) -> str:
    if status == 401:
        return "odds auth rejected"
    if status == 429:
        return "odds quota rejected"
    return f"odds http {status}"


def replay_child_env(parent: dict | None = None) -> dict:
    """Allowlisted runtime env for a Muse or Claude replay child."""
    parent = os.environ if parent is None else parent
    env = {}
    for key in REPLAY_ENV_ALLOW:
        value = parent.get(key)
        if value:
            env[key] = value
    env["LIVE_PROPS_SEND"] = "off"
    env["LIVE_PROPS_ENGINE"] = "on"
    return env


def _scope(params: dict):
    def split(name):
        raw = params.get(name) if isinstance(params, dict) else None
        if not isinstance(raw, str) or raw == "":
            return None
        return [part for part in raw.split(",") if part]

    odds_format = params.get("oddsFormat") if isinstance(params, dict) else None
    if not isinstance(odds_format, str):
        odds_format = None
    return split("markets"), split("regions"), split("bookmakers"), odds_format


def _note(path, params, *, worst, reservation, status, headers, transport_error, logical, attempt):
    try:
        from odds_accounting.boundary import observe_exchange
        markets, regions, bookmakers, odds_format = _scope(params)
        observe_exchange(
            source_job="shadow-collector",
            endpoint_path=path,
            markets=markets,
            regions=regions,
            bookmakers=bookmakers,
            odds_format=odds_format,
            estimated_cost=worst,
            estimate_provenance="caller_worst_case",
            http_status=status,
            headers=headers,
            transport_error=transport_error,
            shadow_reservation_id=reservation,
            logical_request_id=logical,
            attempt_id=attempt,
            attempt_ordinal=1,
        )
    except Exception:
        return


def _odds_request(ledger, transport, api_key: str, path: str, params: dict,
                  kind: str, worst: int, detail: str):
    """Reserve, call once, reconcile. The key is not put in the exception text."""
    reservation = ledger.reserve(kind, worst, detail)
    query = dict(params)
    query["apiKey"] = api_key
    url = ODDS_BASE + path + "?" + urlencode(query)
    logical = uuid.uuid4().hex
    attempt = uuid.uuid4().hex
    try:
        status, headers, body = transport(url)
    except Exception as exc:
        _note(path, params, worst=worst, reservation=reservation, status=None, headers=None,
              transport_error=exc, logical=logical, attempt=attempt)
        ledger.reconcile(reservation, None, error="odds transport failed")
        raise CollectorError("odds transport failed") from exc
    _note(path, params, worst=worst, reservation=reservation, status=status, headers=headers,
          transport_error=None, logical=logical, attempt=attempt)
    if status != 200:
        ledger.reconcile(reservation, headers, error=_safe_odds_error(status))
        raise CollectorError(_safe_odds_error(status))
    ledger.reconcile(reservation, headers)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorError("odds response was not json") from exc
