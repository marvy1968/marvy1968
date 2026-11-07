"""Fail-closed send gate for Muse Prop1.

LIVE_PROPS_SEND=off (also 0, false, no, blank, or any unrecognized value)
blocks Telegram from every entrypoint. Unset is also off. Muse does not copy
Claude Prop 1's historical default, where an unset flag means send.

A send still requires all three:
  mode == alert
  config telegram_enabled == true
  LIVE_PROPS_SEND explicitly on (1 / on / true / yes)

There is no order path. This gate only covers alerts.
"""
from __future__ import annotations

import os

_ON = frozenset({"1", "on", "true", "yes"})


def live_props_send_enabled() -> bool:
    raw = os.environ.get("LIVE_PROPS_SEND")
    if raw is None:
        return False
    return raw.strip().lower() in _ON


def sends_allowed(mode: str | None, cfg: dict | None) -> bool:
    cfg = cfg or {}
    return (
        mode == "alert"
        and bool(cfg.get("telegram_enabled"))
        and live_props_send_enabled()
    )


def shared_snapshot_only() -> bool:
    """When set, the runner must not open a network odds or stats client."""
    raw = os.environ.get("PROP1_SHARED_SNAPSHOT_ONLY", "")
    return raw.strip().lower() in _ON
