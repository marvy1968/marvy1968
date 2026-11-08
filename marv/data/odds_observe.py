"""Fail-closed observe hook for THE ODDS API transports.

Import and storage failures are swallowed here. Callers keep their original
return value and their original exception. This module does not sleep and does
not retry the provider call.

requests follows redirects inside one library call when allow_redirects stays
at its default. Each hop already present on the response is one physical
attempt. urllib3 status retries are not configured on these clients
(max_retries 0), so none are invented here. Noting the same response object
again does not add a row.
"""

import uuid
import weakref

_SEEN = weakref.WeakSet()


def note_exchange(**kwargs):
    try:
        from odds_accounting.boundary import observe_exchange
        observe_exchange(**kwargs)
    except Exception:
        return


def note_hops(*, response=None, transport_error=None, **fields):
    """Record one library result. Redirect hops already on the response count separately."""
    try:
        if response is not None and _seen(response):
            return
        if transport_error is not None and response is None and _seen(transport_error):
            return
        logical = uuid.uuid4().hex
        if transport_error is not None and response is None:
            note_exchange(logical_request_id=logical, attempt_ordinal=1, transport_error=transport_error, **fields)
            return
        hops = []
        if response is not None:
            hops = list(getattr(response, "history", None) or ())
            hops.append(response)
        if not hops:
            note_exchange(logical_request_id=logical, attempt_ordinal=1, **fields)
            return
        for ordinal, hop in enumerate(hops, start=1):
            if hop is not response and _seen(hop):
                continue
            note_exchange(
                logical_request_id=logical,
                attempt_ordinal=ordinal,
                http_status=getattr(hop, "status_code", None),
                headers=getattr(hop, "headers", None),
                **fields,
            )
    except Exception:
        return


def _seen(obj) -> bool:
    try:
        if obj in _SEEN:
            return True
        _SEEN.add(obj)
    except TypeError:
        return False
    return False
