"""CFB line swings + CFB player props are TRACK-ONLY (Marvin approved 2026-10-27, weekly tune-up:
CFB line_swing 31-66 -39.9% ROI, CFB player props 19-63 -30.1%).
Scope: league ncaaf AND (strategy line_swing [pregame or live]  OR  a player prop: strategy value, market player_*,
pregame or in-game). The alert row + paper bet are written exactly as before (still graded, still on the report card);
only the Telegram send is skipped (row stays sent=0). CFB total_swing / live_value / team_trend / game lines, and every
other sport, are unchanged.
Off: CFB_SWING_PROP_TRACK=off | data/CFB_SWING_PROP_TRACK_OFF | /opt/marv-feed/CFB_SWING_PROP_TRACK_OFF
(file switches are checked on every call; no restart needed).
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
REASON = "cfb_swing_prop_track"
PROP_PREFIXES = ("player_",)


def enabled() -> bool:
    if os.environ.get("CFB_SWING_PROP_TRACK", "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not ((_ROOT / "data" / "CFB_SWING_PROP_TRACK_OFF").exists()
                or Path("/opt/marv-feed/CFB_SWING_PROP_TRACK_OFF").exists())


def category(league, strategy, market):
    """'line_swing' / 'player_prop' when this alert is in a track-only CFB category, else None (ignores the switch)."""
    if str(league or "").lower() not in ("ncaaf", "cfb"):
        return None
    st, mk = str(strategy or ""), str(market or "")
    if st == "line_swing":
        return "line_swing"
    if st == "value" and mk.startswith(PROP_PREFIXES):
        return "player_prop"
    return None


def track(league, strategy, market) -> bool:
    """True -> record + grade, but do not send to Telegram."""
    return category(league, strategy, market) is not None and enabled()


def blocked_alert(db, alert_id):
    """Notifier hook: category name when any alert id in alert_id is a track-only CFB alert (switch on), else None."""
    if db is None or alert_id is None or not enabled():
        return None
    ids = [a for a in (alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]) if a is not None]
    for a in ids:
        try:
            row = db.conn.execute("SELECT league, strategy, market FROM alerts WHERE id = ?", (int(a),)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if row is not None:
            cat = category(row[0], row[1], row[2])
            if cat:
                return cat
    return None
