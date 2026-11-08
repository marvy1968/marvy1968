"""Fail-closed observe hook for THE ODDS API transports.

Import and storage failures are swallowed here. Callers keep their original
return value and their original exception.
"""


def note_exchange(**kwargs):
    try:
        from odds_accounting.boundary import observe_exchange
        observe_exchange(**kwargs)
    except Exception:
        return
