"""Shared caps and priorities are not part of this change.

The 2026-11-08 credit audit proposed three later changes. None of the
enforcing parts are implemented here:

1. Skipping paid fetches when a pregame projection is absent, and skipping a
   fresh quote when a cycle only needs final grading. Not implemented.
2. Adaptive live polling and a shared fresh-quote cache. Not implemented.
3. The audit's shared cross-process budget that can reserve credits and
   abstain when a cap is exhausted, plus any priority order among consumers.
   Not implemented.

The approved work is observational accounting only. A future cap or priority
needs a separate approval before any of the functions below may be called
from a request path. They currently raise, and no transport imports them.
"""

ENFORCEMENT_ENABLED = False
ENFORCEMENT_IMPLEMENTED = False


def shared_cap_decision(*_args, **_kwargs):
    """Disabled. Would be the point that skips or reserves a shared cap."""
    raise NotImplementedError(
        "shared cap enforcement is disabled and unimplemented; "
        "separate approval is required before any cap can gate a call")


def priority_decision(*_args, **_kwargs):
    """Disabled. Would reorder or drop consumers."""
    raise NotImplementedError(
        "priority scheduling is disabled and unimplemented; "
        "separate approval is required before any priority can change coverage")


def polling_or_cache_decision(*_args, **_kwargs):
    """Disabled. Would change request frequency or reuse another consumer's quote."""
    raise NotImplementedError(
        "polling and cache changes are disabled and unimplemented; "
        "separate approval is required")
